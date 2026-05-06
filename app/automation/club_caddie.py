import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable, Iterable

import pytesseract
from pywinauto import Desktop, mouse
from pywinauto.application import Application
from pywinauto.findwindows import ElementAmbiguousError, ElementNotFoundError

from app.config import Settings
from app.schemas import SlotStatus, TeeSheetResponse, TeeSlot


class ClubCaddieAutomationError(RuntimeError):
    """Raised when Club Caddie cannot be automated to the requested state."""


class ClubCaddieAutomation:
    TIME_PATTERN = re.compile(r"\b(?:[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM)\b", re.IGNORECASE)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.app: Application | None = None
        self.desktop = Desktop(backend="uia")
        if self.settings.tesseract_executable.exists():
            pytesseract.pytesseract.tesseract_cmd = str(self.settings.tesseract_executable)

    def fetch_tee_sheet(self, target_date: date) -> TeeSheetResponse:
        try:
            self._run_step("attach or launch Club Caddie", self._attach_or_launch)
            self._run_step("close update dialog", self._close_update_dialog_if_present)
            self._run_step("login if needed", self._login_if_needed)
            self._run_step("close post-login update dialog", self._close_update_dialog_if_present)
            self._run_step("open Check-in", self._open_checkin)
            self._run_step("open Tee Sheet", self._open_tee_sheet)
            self._run_step("select tee sheet date", self._select_date, target_date)
            return self._run_step("extract tee sheet", self._extract_tee_sheet, target_date)
        except ClubCaddieAutomationError:
            raise
        except Exception as exc:
            raise ClubCaddieAutomationError(f"Club Caddie automation failed: {exc}") from exc

    def _run_step(self, step_name: str, action: Callable[..., object], *args: object) -> object:
        try:
            return action(*args)
        except ClubCaddieAutomationError:
            raise
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            raise ClubCaddieAutomationError(f"{step_name} failed: {message}") from exc

    def _attach_or_launch(self) -> None:
        executable = self._validate_executable()

        try:
            self.app = Application(backend="uia").connect(path=str(executable))
        except (ElementNotFoundError, ProcessLookupError, RuntimeError):
            self.app = Application(backend="uia").start(str(executable))

        self._wait_for_app_window(timeout_seconds=self.settings.window_wait_seconds)

    def _validate_executable(self) -> Path:
        executable = self.settings.club_caddie_executable
        if not executable.exists():
            raise ClubCaddieAutomationError(f"Club Caddie executable not found: {executable}")
        return executable

    def _close_update_dialog_if_present(self) -> None:
        dialog = self._find_window([r".*Software Update.*"], timeout_seconds=2, app_only=False)
        if dialog is None:
            return

        if self._click_first_match(dialog, [r"^Close$"]):
            self._wait_until_window_gone([r".*Software Update.*"], timeout_seconds=10)

    def _login_if_needed(self) -> None:
        if self._is_tee_sheet_visible():
            return

        login_window = self._find_login_window(timeout_seconds=5)
        if login_window is None:
            return

        self._set_club_code(login_window)
        self._fill_login_edits(login_window)

        if not self._click_first_match(login_window, [r"^LOGIN$", r"^Login$"]):
            raise ClubCaddieAutomationError("Login button was not found.")

        self._wait_for_any_window(
            title_patterns=[r".*Rolling.*", r".*Club.*", r".*Register.*", r".*Tee.*"],
            timeout_seconds=self.settings.window_wait_seconds,
        )

    def _find_login_window(self, timeout_seconds: int) -> object | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for window in self._visible_windows(app_only=True):
                visible_edits = [
                    edit
                    for edit in self._descendants(window, control_type="Edit")
                    if self._is_actionable(edit)
                ]
                has_visible_login_button = any(
                    self._text_matches(control, [r"^LOGIN$", r"^Login$"])
                    for control in self._descendants(window, control_type="Button")
                    if self._is_actionable(control)
                )
                if len(visible_edits) >= 2 and has_visible_login_button:
                    return window
            time.sleep(0.5)
        return None

    def _set_club_code(self, login_window: object) -> None:
        combo_boxes = [
            combo
            for combo in self._descendants(login_window, control_type="ComboBox")
            if self._is_actionable(combo)
        ]
        if not combo_boxes:
            return

        combo = combo_boxes[0]
        if self.settings.club_code.lower() in self._text_of(combo).lower():
            return

        try:
            combo.select(self.settings.club_code)
        except Exception:
            try:
                combo.set_edit_text(self.settings.club_code)
            except Exception:
                combo.click_input()
                combo.type_keys("^a{BACKSPACE}" + self.settings.club_code, set_foreground=True)

    def _fill_login_edits(self, login_window: object) -> None:
        edits = [
            edit
            for edit in self._descendants(login_window, control_type="Edit")
            if self._is_actionable(edit)
        ]
        if len(edits) < 2:
            raise ClubCaddieAutomationError("Login username/password fields were not found.")

        self._set_edit_text(edits[0], self.settings.username)
        self._set_edit_text(edits[1], self.settings.password)

    def _set_edit_text(self, edit_control: object, value: str) -> None:
        try:
            edit_control.set_edit_text(value)
        except Exception:
            edit_control.click_input()
            edit_control.type_keys("^a{BACKSPACE}" + value, set_foreground=True)

    def _open_checkin(self) -> None:
        if self._is_tee_sheet_visible():
            return

        home_window = self._find_window(
            [r".*Rolling.*", r".*Club.*", r".*Caddie.*"],
            timeout_seconds=self.settings.window_wait_seconds,
        )
        if home_window is None:
            raise ClubCaddieAutomationError("Home window was not found after login.")

        if self._window_contains_text(home_window, ["TEE SHEET", "REGISTER"]):
            return

        self._focus_window(home_window)
        if not self._click_near_text(home_window, [r"Check\s*-?\s*in", r"Checkin"], y_offset=-35):
            raise ClubCaddieAutomationError("Checkin button was not found on the home window.")

        self._wait_for_any_content(
            required_text_groups=[["REGISTER"], ["TEE SHEET"], ["QUICKMENU"]],
            timeout_seconds=self.settings.window_wait_seconds,
        )

    def _open_tee_sheet(self) -> None:
        if self._is_tee_sheet_visible():
            return

        register_window = self._find_window(
            [r".*Register.*", r".*Rolling.*", r".*Club.*"],
            timeout_seconds=self.settings.window_wait_seconds,
        )
        if register_window is None:
            raise ClubCaddieAutomationError("Register window was not found.")

        if not self._click_first_match(register_window, [r"TEE\s*SHEET", r"Tee\s*Sheet"]):
            raise ClubCaddieAutomationError("Tee Sheet button was not found.")

        self._wait_for_content(["TEE SHEET", "View By"], timeout_seconds=self.settings.window_wait_seconds)

    def _select_date(self, target_date: date) -> None:
        tee_window = self._current_tee_sheet_window()
        date_text = f"{target_date.month}/{target_date.day}/{target_date.year}"

        edits = [
            edit
            for edit in self._descendants(tee_window, control_type="Edit")
            if self._is_visible(edit)
        ]
        if any(self._text_of(edit) == date_text for edit in edits):
            time.sleep(1)
            return

        candidate_edits = [
            edit for edit in edits if self._looks_like_date_field(self._text_of(edit))
        ]
        target_edit = candidate_edits[0] if candidate_edits else edits[0] if edits else None

        if target_edit is None:
            raise ClubCaddieAutomationError("Tee Sheet date input was not found.")
        if not self._is_enabled(target_edit):
            try:
                self._select_date_with_calendar(tee_window, target_edit, target_date)
            except ClubCaddieAutomationError:
                self._navigate_date_with_arrows(tee_window, target_edit, target_date)
            return

        self._set_edit_text(target_edit, date_text)
        target_edit.type_keys("{ENTER}", set_foreground=True)
        time.sleep(2)

    def _looks_like_date_field(self, text: str) -> bool:
        return bool(re.search(r"\d{1,2}/\d{1,2}/\d{4}", text))

    def _select_date_with_calendar(
        self,
        tee_window: object,
        date_edit: object,
        target_date: date,
    ) -> None:
        current_date = self._parse_visible_date(date_edit)
        self._open_calendar(tee_window)

        month_delta = (target_date.year - current_date.year) * 12
        month_delta += target_date.month - current_date.month
        month_button_pattern = r"^Next button$" if month_delta > 0 else r"^Previous button$"

        for _ in range(abs(month_delta)):
            if not self._click_first_match(tee_window, [month_button_pattern]):
                raise ClubCaddieAutomationError("Calendar month navigation button was not found.")
            time.sleep(0.2)

        target_label = self._calendar_day_label(target_date)
        if not self._click_first_match(tee_window, [f"^{re.escape(target_label)}$"]):
            raise ClubCaddieAutomationError(f"Calendar day button was not found: {target_label}")

        self._wait_for_visible_date(tee_window, target_date)

    def _open_calendar(self, tee_window: object) -> None:
        if self._is_calendar_visible(tee_window):
            return

        if not self._click_first_match(tee_window, [r"^Show Calendar$"]):
            raise ClubCaddieAutomationError("Show Calendar button was not found.")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._is_calendar_visible(tee_window):
                return
            time.sleep(0.25)

        raise ClubCaddieAutomationError("Calendar did not open.")

    def _is_calendar_visible(self, tee_window: object) -> bool:
        return any(
            self._is_visible(calendar)
            for calendar in self._descendants(tee_window, control_type="Calendar")
        )

    def _calendar_day_label(self, target_date: date) -> str:
        return (
            f"{target_date.strftime('%A')}, "
            f"{target_date.strftime('%B')} "
            f"{target_date.day}, "
            f"{target_date.year}"
        )

    def _navigate_date_with_arrows(
        self,
        tee_window: object,
        date_edit: object,
        target_date: date,
    ) -> None:
        current_date = self._parse_visible_date(date_edit)
        day_delta = (target_date - current_date).days
        if day_delta == 0:
            return

        prev_button, next_button = self._date_arrow_buttons(tee_window, date_edit)
        button = next_button if day_delta > 0 else prev_button

        self._focus_window(tee_window)
        for _ in range(abs(day_delta)):
            rectangle = self._safe_rectangle(button)
            if rectangle is None:
                raise ClubCaddieAutomationError("Date navigation button disappeared.")
            x = (rectangle.left + rectangle.right) // 2
            y = (rectangle.top + rectangle.bottom) // 2
            mouse.click(button="left", coords=(x, y))
            time.sleep(0.04)

        self._wait_for_visible_date(tee_window, target_date)

    def _parse_visible_date(self, date_edit: object) -> date:
        text = self._text_of(date_edit)
        try:
            return datetime.strptime(text, "%m/%d/%Y").date()
        except ValueError as exc:
            raise ClubCaddieAutomationError(f"Unable to parse visible Tee Sheet date: {text}") from exc

    def _date_arrow_buttons(self, tee_window: object, date_edit: object) -> tuple[object, object]:
        date_rect = self._safe_rectangle(date_edit)
        if date_rect is None:
            raise ClubCaddieAutomationError("Tee Sheet date field position was not available.")

        buttons = []
        for button in self._descendants(tee_window, control_type="Button"):
            if not self._is_actionable(button) or self._text_of(button):
                continue

            rectangle = self._safe_rectangle(button)
            if rectangle is None:
                continue

            vertically_aligned = abs(
                ((rectangle.top + rectangle.bottom) // 2)
                - ((date_rect.top + date_rect.bottom) // 2)
            ) <= 20
            right_of_date_field = rectangle.left > date_rect.right
            close_to_date_field = rectangle.left < date_rect.right + 120
            if vertically_aligned and right_of_date_field and close_to_date_field:
                buttons.append(button)

        buttons.sort(key=lambda item: self._safe_rectangle(item).left)
        if len(buttons) < 2:
            raise ClubCaddieAutomationError("Tee Sheet previous/next date buttons were not found.")
        return buttons[0], buttons[1]

    def _wait_for_visible_date(self, tee_window: object, target_date: date) -> None:
        expected = f"{target_date.month}/{target_date.day}/{target_date.year}"
        deadline = time.monotonic() + self.settings.window_wait_seconds
        while time.monotonic() < deadline:
            for edit in self._descendants(tee_window, control_type="Edit"):
                if self._is_visible(edit) and self._text_of(edit) == expected:
                    time.sleep(1)
                    return
            time.sleep(0.25)

        raise ClubCaddieAutomationError(f"Timed out waiting for Tee Sheet date {expected}.")

    def _extract_tee_sheet(self, target_date: date) -> TeeSheetResponse:
        tee_window = self._current_tee_sheet_window()
        texts = self._extract_visible_text_items(tee_window)
        side_split_x = self._window_midpoint_x(tee_window)
        front = self._extract_slots_for_side(texts, "front", side_split_x)
        back = self._extract_slots_for_side(texts, "back", side_split_x)
        warnings = []
        used_ocr = False

        if not front and not back:
            try:
                front, back = self._extract_slots_from_screenshot(tee_window)
                used_ocr = True
            except Exception as exc:
                warnings.append(f"Screenshot/OCR fallback failed: {exc}")

        if used_ocr and (front or back):
            warnings.append("Tee sheet grid text was extracted with screenshot/OCR fallback.")
        if not front and not back:
            warnings.append("No tee slots were exposed through UI Automation or OCR.")

        return TeeSheetResponse(
            date=target_date,
            club_code=self.settings.club_code,
            front=front,
            back=back,
            extracted_at=datetime.now(UTC),
            warnings=warnings,
        )

    def _extract_slots_from_screenshot(self, tee_window: object) -> tuple[list[TeeSlot], list[TeeSlot]]:
        self._focus_window(tee_window)
        time.sleep(0.5)
        image = tee_window.capture_as_image()
        width, height = image.size
        grid_top = int(height * 0.24)
        grid_bottom = int(height * 0.97)
        midpoint = width // 2

        full_grid_image = image.crop((0, int(height * 0.20), width, grid_bottom))
        front, back = self._ocr_slots_from_full_grid(full_grid_image)
        if front or back:
            return front, back

        front_image = image.crop((0, grid_top, midpoint, grid_bottom))
        back_image = image.crop((midpoint, grid_top, width, grid_bottom))

        return (
            self._ocr_slots_for_region(front_image, "front"),
            self._ocr_slots_for_region(back_image, "back"),
        )

    def _ocr_slots_from_full_grid(self, image: object) -> tuple[list[TeeSlot], list[TeeSlot]]:
        scaled = image.convert("L").resize((image.size[0] * 2, image.size[1] * 2))
        ocr_text = pytesseract.image_to_string(scaled, config="--psm 6")
        lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
        front: dict[str, TeeSlot] = {}
        back: dict[str, TeeSlot] = {}

        for line in lines:
            matches = list(self.TIME_PATTERN.finditer(line))
            if len(matches) < 2:
                continue

            for side, match in zip(("front", "back"), matches[:2], strict=True):
                time_label = self._normalize_time(match.group(0))
                if not self._is_plausible_tee_time(time_label):
                    continue

                next_match = matches[1] if side == "front" else None
                raw_text = line[match.start() : next_match.start() if next_match else None].strip()
                slot = TeeSlot(
                    time=time_label,
                    side=side,
                    status=self._classify_slot(raw_text),
                    raw_text=raw_text,
                    player_or_note=self._extract_note(raw_text, time_label),
                    source="screenshot",
                    confidence=0.75,
                )

                if side == "front":
                    front.setdefault(time_label, slot)
                else:
                    back.setdefault(time_label, slot)

        return list(front.values()), list(back.values())

    def _ocr_slots_for_region(self, image: object, side: str) -> list[TeeSlot]:
        scaled = image.convert("L").resize((image.size[0] * 3, image.size[1] * 3))
        ocr_text = pytesseract.image_to_string(scaled, config="--psm 11")
        lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
        slots: dict[str, TeeSlot] = {}

        for index, line_text in enumerate(lines):
            for match in self.TIME_PATTERN.finditer(line_text):
                time_label = self._normalize_time(match.group(0))
                if not self._is_plausible_tee_time(time_label):
                    continue

                following_text = self._text_until_next_time(lines[index + 1 : index + 4])
                raw_text = " | ".join(part for part in [line_text, following_text] if part)
                slots.setdefault(
                    time_label,
                    TeeSlot(
                        time=time_label,
                        side=side,
                        status=self._classify_slot(raw_text),
                        raw_text=raw_text,
                        player_or_note=self._extract_note(raw_text, time_label),
                        source="screenshot",
                        confidence=0.65,
                    ),
                )

        return list(slots.values())

    def _text_until_next_time(self, lines: list[str]) -> str:
        selected = []
        for line in lines:
            if self.TIME_PATTERN.search(line) or self._looks_like_ocr_time(line):
                break
            selected.append(line)
        return " | ".join(selected)

    def _looks_like_ocr_time(self, line: str) -> bool:
        return bool(re.search(r"\b\d{1,2}\s*[:;]\s*\d{2}\s*(?:am|pm|em|om)?\b", line, re.IGNORECASE))

    def _is_plausible_tee_time(self, time_label: str) -> bool:
        parsed = datetime.strptime(time_label, "%I:%M %p")
        if parsed.strftime("%p") == "AM":
            return parsed.hour >= 5
        return parsed.hour <= 21

    def _extract_slots_for_side(
        self,
        text_items: list[dict[str, object]],
        side: str,
        side_split_x: int,
    ) -> list[TeeSlot]:
        slots: dict[str, TeeSlot] = {}
        side_items = [
            item
            for item in text_items
            if self._item_side(item, side_split_x) == side
        ]

        for item in side_items:
            text = str(item["text"]).strip()
            for match in self.TIME_PATTERN.finditer(text):
                time_label = self._normalize_time(match.group(0))
                raw_text = self._nearby_text(item, side_items)
                slots.setdefault(
                    time_label,
                    TeeSlot(
                        time=time_label,
                        side=side,
                        status=self._classify_slot(raw_text),
                        raw_text=raw_text,
                        player_or_note=self._extract_note(raw_text, time_label),
                    ),
                )

        return list(slots.values())

    def _extract_visible_text_items(self, root_control: object) -> list[dict[str, object]]:
        items = []
        for control in [root_control, *self._descendants(root_control)]:
            text = self._text_of(control)
            if not text:
                continue

            rectangle = self._safe_rectangle(control)
            if rectangle is None:
                continue

            items.append(
                {
                    "text": text,
                    "left": rectangle.left,
                    "right": rectangle.right,
                    "top": rectangle.top,
                    "bottom": rectangle.bottom,
                }
            )
        return items

    def _nearby_text(self, item: dict[str, object], side_items: list[dict[str, object]]) -> str:
        top = int(item["top"])
        bottom = int(item["bottom"])
        row_items = [
            str(candidate["text"]).strip()
            for candidate in side_items
            if abs(int(candidate["top"]) - top) <= 20
            or abs(int(candidate["bottom"]) - bottom) <= 20
        ]
        return " | ".join(dict.fromkeys(row_items))

    def _classify_slot(self, raw_text: str) -> SlotStatus:
        normalized = raw_text.lower()
        if "blocked" in normalized or "not available" in normalized:
            return SlotStatus.BLOCKED
        if "reserved" in normalized or "booked" in normalized or "no show" in normalized:
            return SlotStatus.BOOKED
        if "add" in normalized and not self._has_non_button_booking_text(raw_text):
            return SlotStatus.AVAILABLE
        if self._has_non_button_booking_text(raw_text):
            return SlotStatus.BOOKED
        return SlotStatus.AVAILABLE

    def _has_non_button_booking_text(self, raw_text: str) -> bool:
        ignored = {
            "add",
            "ae",
            "am",
            "back",
            "blue",
            "by",
            "coreamy",
            "date",
            "ece",
            "eeg",
            "ege",
            "ere",
            "etre",
            "front",
            "ieee",
            "pm",
            "sec",
            "tee",
            "view",
        }
        without_times = self.TIME_PATTERN.sub("", raw_text.lower())
        tokens = re.findall(r"[a-z0-9]+", without_times)
        meaningful = [token for token in tokens if token not in ignored and not token.isdigit()]
        return bool(meaningful)

    def _extract_note(self, raw_text: str, time_label: str) -> str | None:
        note = self.TIME_PATTERN.sub("", raw_text)
        note = re.sub(r"\badd\b", "", note, flags=re.IGNORECASE)
        for artifact in ("ae", "ece", "eeg", "ege", "ere", "etre", "ieee", "coreamy", "sec"):
            note = re.sub(rf"\b{artifact}\b", "", note, flags=re.IGNORECASE)
        note = re.sub(r"[\[\](){}]", "", note)
        note = re.sub(r"\s+", " ", note.replace("|", " ")).strip()
        return note or None

    def _is_time_label(self, value: str, time_label: str) -> bool:
        if not self.TIME_PATTERN.search(value):
            return False
        return self._normalize_time(value) == time_label

    def _normalize_time(self, value: str) -> str:
        value = value.strip().upper().replace(" ", "")
        parsed = datetime.strptime(value, "%I:%M%p")
        return parsed.strftime("%I:%M %p").lstrip("0")

    def _item_side(self, item: dict[str, object], split_x: int) -> str:
        left = int(item["left"])
        right = int(item["right"])
        midpoint = (left + right) // 2
        return "front" if midpoint < split_x else "back"

    def _window_midpoint_x(self, window: object) -> int:
        rectangle = self._safe_rectangle(window)
        if rectangle is None:
            return 0
        return (rectangle.left + rectangle.right) // 2

    def _is_tee_sheet_visible(self) -> bool:
        window = self._find_window([r".*Tee.*", r".*Rolling.*", r".*Club.*"], timeout_seconds=2)
        return window is not None and self._window_contains_text(window, ["TEE SHEET", "View By"])

    def _current_tee_sheet_window(self) -> object:
        window = self._find_window(
            [r".*Tee.*", r".*Rolling.*", r".*Club.*"],
            timeout_seconds=self.settings.window_wait_seconds,
        )
        if window is None or not self._window_contains_text(window, ["TEE SHEET"]):
            raise ClubCaddieAutomationError("Tee Sheet window is not visible.")
        return window

    def _wait_for_content(self, required_texts: Iterable[str], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        required = [text.upper() for text in required_texts]

        while time.monotonic() < deadline:
            for window in self._visible_windows(app_only=True):
                combined = "\n".join(self._control_texts(window)).upper()
                if all(text in combined for text in required):
                    return
            time.sleep(0.5)

        raise ClubCaddieAutomationError(f"Timed out waiting for content: {', '.join(required_texts)}")

    def _wait_for_any_content(
        self,
        required_text_groups: Iterable[Iterable[str]],
        timeout_seconds: int,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        required_groups = [
            [text.upper() for text in required_texts]
            for required_texts in required_text_groups
        ]

        while time.monotonic() < deadline:
            for window in self._visible_windows(app_only=True):
                combined = "\n".join(self._control_texts(window)).upper()
                if any(all(text in combined for text in group) for group in required_groups):
                    return
            time.sleep(0.5)

        formatted_groups = [" + ".join(group) for group in required_groups]
        raise ClubCaddieAutomationError(
            f"Timed out waiting for any content group: {', '.join(formatted_groups)}"
        )

    def _wait_for_any_window(self, title_patterns: Iterable[str], timeout_seconds: int) -> object:
        window = self._find_window(title_patterns, timeout_seconds)
        if window is None:
            raise ClubCaddieAutomationError("Timed out waiting for Club Caddie window.")
        return window

    def _wait_for_app_window(self, timeout_seconds: int) -> object:
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            if self.app is not None:
                try:
                    windows = [window for window in self.app.windows() if window.is_visible()]
                except (ElementAmbiguousError, ElementNotFoundError, RuntimeError):
                    windows = []
                if windows:
                    return windows[0]
            time.sleep(0.5)

        raise ClubCaddieAutomationError("Timed out waiting for Club Caddie to open a window.")

    def _wait_until_window_gone(self, title_patterns: Iterable[str], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._find_window(title_patterns, timeout_seconds=1) is None:
                return
            time.sleep(0.25)

    def _find_window(
        self,
        title_patterns: Iterable[str],
        timeout_seconds: int,
        app_only: bool = True,
    ) -> object | None:
        deadline = time.monotonic() + timeout_seconds
        patterns = [re.compile(pattern, re.IGNORECASE) for pattern in title_patterns]

        while time.monotonic() < deadline:
            for window in self._visible_windows(app_only=app_only):
                title = self._text_of(window)
                if any(pattern.search(title) for pattern in patterns):
                    return window
            time.sleep(0.25)

        return None

    def _visible_windows(self, app_only: bool = True) -> list[object]:
        try:
            if app_only and self.app is not None:
                return [window for window in self.app.windows() if window.is_visible()]
            return [window for window in self.desktop.windows() if window.is_visible()]
        except (ElementAmbiguousError, ElementNotFoundError, RuntimeError):
            return []

    def _click_first_match(self, root_control: object, text_patterns: Iterable[str]) -> bool:
        controls = self._descendants(root_control)

        for control in controls:
            if self._is_actionable(control) and self._text_matches(control, text_patterns):
                try:
                    self._invoke_or_click(control)
                    time.sleep(0.5)
                    return True
                except Exception:
                    continue

        return False

    def _text_matches(self, control: object, text_patterns: Iterable[str]) -> bool:
        text = self._text_of(control)
        if not text:
            return False
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in text_patterns)

    def _click_near_text(
        self,
        root_control: object,
        text_patterns: Iterable[str],
        y_offset: int = 0,
    ) -> bool:
        for control in self._descendants(root_control):
            if not self._is_visible(control) or not self._text_matches(control, text_patterns):
                continue

            rectangle = self._safe_rectangle(control)
            if rectangle is None:
                continue

            x = (rectangle.left + rectangle.right) // 2
            y = (rectangle.top + rectangle.bottom) // 2 + y_offset
            mouse.click(button="left", coords=(x, y))
            time.sleep(0.5)
            return True

        return False

    def _invoke_or_click(self, control: object) -> None:
        try:
            control.invoke()
            return
        except Exception:
            pass
        control.click_input()

    def _window_contains_text(self, window: object, expected_texts: Iterable[str]) -> bool:
        combined = "\n".join(self._control_texts(window)).upper()
        return all(expected.upper() in combined for expected in expected_texts)

    def _control_texts(self, root_control: object) -> list[str]:
        texts = []
        for control in [root_control, *self._descendants(root_control)]:
            if control is not root_control and not self._is_visible(control):
                continue
            text = self._text_of(control)
            if text:
                texts.append(text)
        return texts

    def _descendants(self, root_control: object, control_type: str | None = None) -> list[object]:
        try:
            if control_type is None:
                return root_control.descendants()
            return root_control.descendants(control_type=control_type)
        except (ElementAmbiguousError, ElementNotFoundError, RuntimeError):
            return []

    def _text_of(self, control: object) -> str:
        for getter_name in ("window_text", "element_info"):
            try:
                if getter_name == "window_text":
                    text = control.window_text()
                else:
                    text = control.element_info.name
            except (AttributeError, RuntimeError):
                continue
            if text:
                return str(text).strip()
        return ""

    def _is_visible(self, control: object) -> bool:
        try:
            return bool(control.is_visible())
        except RuntimeError:
            return False

    def _is_enabled(self, control: object) -> bool:
        try:
            return bool(control.is_enabled())
        except RuntimeError:
            return False

    def _is_actionable(self, control: object) -> bool:
        return self._is_visible(control) and self._is_enabled(control)

    def _safe_rectangle(self, control: object) -> object | None:
        try:
            return control.rectangle()
        except RuntimeError:
            return None

    def _focus_window(self, window: object) -> None:
        try:
            window.set_focus()
        except RuntimeError:
            try:
                window.click_input()
            except RuntimeError:
                return
