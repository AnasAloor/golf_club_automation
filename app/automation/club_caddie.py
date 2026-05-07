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
from app.schemas import (
    BookingStatus,
    TeeSheetBookingRequest,
    TeeSheetBookingResponse,
    SlotStatus,
    TeeSheetResponse,
    TeeSlot,
)


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

    def book_tee_time(self, booking: TeeSheetBookingRequest) -> TeeSheetBookingResponse:
        try:
            self._run_step("attach or launch Club Caddie", self._attach_or_launch)
            self._run_step("close update dialog", self._close_update_dialog_if_present)
            self._run_step("close existing booking modal", self._close_booking_modal_if_present)
            self._run_step("login if needed", self._login_if_needed)
            self._run_step("close post-login update dialog", self._close_update_dialog_if_present)
            self._run_step("open Check-in", self._open_checkin)
            self._run_step("open Tee Sheet", self._open_tee_sheet)
            self._run_step("select tee sheet date", self._select_date, booking.date)
            self._run_step("click booking add button", self._click_slot_add_button, booking)
            self._run_step("fill booking modal", self._fill_booking_modal, booking)

            if booking.dry_run:
                return self._booking_response(
                    booking,
                    BookingStatus.UNKNOWN,
                    "Booking form filled successfully; dry_run skipped Reserve.",
                    ["dry_run was enabled, so Reserve was not clicked."],
                )

            self._run_step("submit reservation", self._submit_booking_modal, booking)
            return self._booking_response(
                booking,
                BookingStatus.RESERVED,
                "Reservation submitted successfully.",
                [],
            )
        except ClubCaddieAutomationError:
            raise
        except Exception as exc:
            raise ClubCaddieAutomationError(f"Club Caddie booking automation failed: {exc}") from exc

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
        except Exception:
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
            [r".*Tee.*", r".*Rolling.*", r".*Club.*", r".*Caddie.*"],
            timeout_seconds=self.settings.window_wait_seconds,
        )
        if home_window is None:
            app_windows = self._visible_windows(app_only=True)
            home_window = app_windows[0] if app_windows else None
        if home_window is None:
            raise ClubCaddieAutomationError("Home window was not found after login.")

        if self._window_contains_any_text(home_window, ["TEE SHEET", "REGISTER"]):
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
        if self._is_enabled(target_edit):
            try:
                self._set_edit_text(target_edit, date_text)
                target_edit.type_keys("{ENTER}", set_foreground=True)
                self._wait_for_visible_date(tee_window, target_date, timeout_seconds=5)
                return
            except ClubCaddieAutomationError:
                pass

        try:
            self._select_date_with_calendar(tee_window, target_edit, target_date)
            return
        except ClubCaddieAutomationError:
            self._navigate_date_with_arrows(tee_window, target_edit, target_date)

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
            if not self._click_calendar_button_by_position(tee_window):
                raise ClubCaddieAutomationError("Show Calendar button was not found.")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._is_calendar_visible(tee_window):
                return
            time.sleep(0.25)

        raise ClubCaddieAutomationError("Calendar did not open.")

    def _click_calendar_button_by_position(self, tee_window: object) -> bool:
        date_edits = [
            edit
            for edit in self._descendants(tee_window, control_type="Edit")
            if self._is_visible(edit) and self._looks_like_date_field(self._text_of(edit))
        ]
        if not date_edits:
            return False

        date_rect = self._safe_rectangle(date_edits[0])
        if date_rect is None:
            return False

        for button in self._descendants(tee_window, control_type="Button"):
            rectangle = self._safe_rectangle(button)
            if rectangle is None or not self._is_visible(button):
                continue
            vertically_aligned = abs(
                ((rectangle.top + rectangle.bottom) // 2)
                - ((date_rect.top + date_rect.bottom) // 2)
            ) <= 20
            near_right_edge = date_rect.right <= rectangle.left <= date_rect.right + 40
            if vertically_aligned and near_right_edge:
                x = (rectangle.left + rectangle.right) // 2
                y = (rectangle.top + rectangle.bottom) // 2
                mouse.click(button="left", coords=(x, y))
                return True

        return False

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

    def _wait_for_visible_date(
        self,
        tee_window: object,
        target_date: date,
        timeout_seconds: int | None = None,
    ) -> None:
        expected = f"{target_date.month}/{target_date.day}/{target_date.year}"
        deadline = time.monotonic() + (timeout_seconds or self.settings.window_wait_seconds)
        while time.monotonic() < deadline:
            for edit in self._descendants(tee_window, control_type="Edit"):
                if self._is_visible(edit) and self._text_of(edit) == expected:
                    time.sleep(1)
                    return
            time.sleep(0.25)

        raise ClubCaddieAutomationError(f"Timed out waiting for Tee Sheet date {expected}.")

    def _extract_tee_sheet(self, target_date: date) -> TeeSheetResponse:
        tee_window = self._current_tee_sheet_window()
        front: list[TeeSlot] = []
        back: list[TeeSlot] = []
        warnings = []
        used_ocr = False

        try:
            front, back = self._extract_slots_from_screenshot(tee_window)
            used_ocr = bool(front or back)
        except Exception as exc:
            warnings.append(f"Screenshot/OCR fallback failed: {exc}")

        if not front and not back:
            texts = self._extract_visible_text_items(tee_window)
            side_split_x = self._window_midpoint_x(tee_window)
            front = self._extract_slots_for_side(texts, "front", side_split_x)
            back = self._extract_slots_for_side(texts, "back", side_split_x)

        front, back, inferred_count = self._balance_side_slots(front, back)
        if inferred_count:
            warnings.append(
                f"{inferred_count} slot(s) were inferred from the opposite side because OCR missed a side-specific row."
            )

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

    def _click_slot_add_button(self, booking: TeeSheetBookingRequest) -> None:
        target_time = self._normalize_time(booking.time)
        tee_window = self._current_tee_sheet_window()
        self._focus_window(tee_window)
        self._reset_tee_sheet_scroll(tee_window)
        seen_signatures: set[tuple[str, str]] = set()
        repeated_pages = 0

        for _ in range(90):
            image = tee_window.capture_as_image()
            click_coords = self._find_add_button_coords_by_geometry(image, booking.side, target_time)
            if click_coords is None:
                click_coords = self._find_add_button_coords(image, booking.side, target_time)
            if click_coords is not None:
                mouse.click(button="left", coords=click_coords)
                if not self._wait_for_booking_modal_after_click(timeout_seconds=3):
                    mouse.double_click(button="left", coords=click_coords)
                    self._wait_for_any_booking_modal()
                return

            front, back = self._ocr_slots_from_full_grid(
                image.crop((0, int(image.size[1] * 0.20), image.size[0], int(image.size[1] * 0.97)))
            )
            signature = (
                ",".join(slot.time for slot in front),
                ",".join(slot.time for slot in back),
            )
            if signature in seen_signatures:
                repeated_pages += 1
            else:
                seen_signatures.add(signature)
                repeated_pages = 0
            if repeated_pages >= 5:
                break

            self._scroll_tee_sheet_down(tee_window)

        fallback_coords = self._find_any_visible_available_slot_coords(tee_window, booking.side)
        if fallback_coords is not None:
            mouse.double_click(button="left", coords=fallback_coords)
            self._wait_for_any_booking_modal()
            return

        raise ClubCaddieAutomationError(
            f"Available Add button was not found for {booking.side} {target_time}."
        )

    def _find_any_visible_available_slot_coords(
        self,
        tee_window: object,
        side: str,
    ) -> tuple[int, int] | None:
        self._reset_tee_sheet_scroll(tee_window)
        rectangle = self._safe_rectangle(tee_window)
        if rectangle is None:
            return None

        side_left = rectangle.left if side == "front" else (rectangle.left + rectangle.right) // 2
        side_width = (rectangle.right - rectangle.left) // 2
        x = int(side_left + side_width * 0.25)
        first_slot_y = int(rectangle.top + (rectangle.bottom - rectangle.top) * 0.36)
        return x, first_slot_y

    def _find_add_button_coords_by_geometry(
        self,
        image: object,
        side: str,
        target_time: str,
    ) -> tuple[int, int] | None:
        width, height = image.size
        side_left = 0 if side == "front" else width // 2
        side_width = width // 2
        side_image = image.crop((side_left, 0, side_left + side_width, height))
        scaled = side_image.convert("L").resize((side_image.size[0] * 2, side_image.size[1] * 2))
        ocr_data = pytesseract.image_to_data(scaled, output_type=pytesseract.Output.DICT, config="--psm 11")
        time_words = []
        add_words = []

        for index, text in enumerate(ocr_data["text"]):
            word_text = str(text).strip()
            left = int(ocr_data["left"][index]) / 2 + side_left
            top = int(ocr_data["top"][index]) / 2
            word_width = int(ocr_data["width"][index]) / 2
            word_height = int(ocr_data["height"][index]) / 2
            center_y = top + word_height / 2

            if self._ocr_time_matches_target(word_text, target_time):
                time_words.append((left, center_y))
            elif word_text.lower() == "add":
                add_words.append((left + word_width / 2, center_y))

        if not time_words:
            return None

        time_left, row_center_y = sorted(time_words, key=lambda item: item[0])[0]
        row_add_words = [
            (add_x, add_y)
            for add_x, add_y in add_words
            if add_x > time_left and abs(add_y - row_center_y) <= 30
        ]
        if row_add_words:
            add_x, add_y = sorted(row_add_words, key=lambda item: item[0])[0]
            return int(add_x), int(add_y)

        return int(time_left + 80), int(row_center_y)

    def _find_add_button_coords(
        self,
        image: object,
        side: str,
        target_time: str,
    ) -> tuple[int, int] | None:
        width, height = image.size
        crop_top = int(height * 0.20)
        crop_bottom = int(height * 0.97)
        side_left = 0 if side == "front" else width // 2
        side_right = width // 2 if side == "front" else width
        side_image = image.crop((side_left, crop_top, side_right, crop_bottom))
        scaled = side_image.convert("L").resize((side_image.size[0] * 2, side_image.size[1] * 2))
        ocr_data = pytesseract.image_to_data(scaled, output_type=pytesseract.Output.DICT, config="--psm 6")
        time_rows = self._ocr_word_rows(ocr_data, scale=2, x_offset=side_left, y_offset=crop_top)

        for row in time_rows:
            row_text = " ".join(word["text"] for word in row)
            if not self._ocr_time_matches_target(row_text, target_time):
                continue
            if "add" not in row_text.lower():
                raise ClubCaddieAutomationError(f"Slot {side} {target_time} does not appear available.")

            add_words = [word for word in row if word["text"].lower() == "add"]
            add_word = add_words[0] if add_words else row[-1]
            return int(add_word["center_x"]), int(add_word["center_y"])

        return None

    def _ocr_word_rows(
        self,
        ocr_data: dict[str, list[object]],
        scale: int,
        x_offset: int,
        y_offset: int,
    ) -> list[list[dict[str, object]]]:
        rows: dict[tuple[int, int, int], list[dict[str, object]]] = {}
        for index, text in enumerate(ocr_data["text"]):
            word_text = str(text).strip()
            if not word_text:
                continue

            key = (
                int(ocr_data["block_num"][index]),
                int(ocr_data["par_num"][index]),
                int(ocr_data["line_num"][index]),
            )
            left = int(ocr_data["left"][index]) / scale + x_offset
            top = int(ocr_data["top"][index]) / scale + y_offset
            width = int(ocr_data["width"][index]) / scale
            height = int(ocr_data["height"][index]) / scale
            rows.setdefault(key, []).append(
                {
                    "text": word_text,
                    "center_x": left + width / 2,
                    "center_y": top + height / 2,
                }
            )

        return [sorted(words, key=lambda word: float(word["center_x"])) for words in rows.values()]

    def _normalize_time_or_none(self, text: str) -> str | None:
        match = self.TIME_PATTERN.search(text)
        if match is None:
            return None
        return self._normalize_time(match.group(0))

    def _ocr_time_matches_target(self, text: str, target_time: str) -> bool:
        normalized_time = self._normalize_time_or_none(text)
        if normalized_time is not None:
            return normalized_time == target_time

        time_without_meridiem = re.search(r"\b(?:[1-9]|1[0-2]):[0-5]\d\b", text)
        if time_without_meridiem is None:
            return False

        return time_without_meridiem.group(0).lstrip("0") == target_time.rsplit(" ", 1)[0].lstrip("0")

    def _wait_for_booking_modal(self, target_time: str) -> object:
        deadline = time.monotonic() + self.settings.window_wait_seconds
        while time.monotonic() < deadline:
            for window in self._visible_windows(app_only=True):
                texts = self._control_texts(window)
                combined = "\n".join(texts).upper()
                if target_time.upper() in combined and "RESERVE" in combined and "SELECT PLAYERS" in combined:
                    return window
            time.sleep(0.5)
        raise ClubCaddieAutomationError("Booking modal did not open.")

    def _wait_for_any_booking_modal(self) -> object:
        deadline = time.monotonic() + self.settings.window_wait_seconds
        while time.monotonic() < deadline:
            try:
                return self._current_booking_modal()
            except ClubCaddieAutomationError:
                time.sleep(0.5)
        raise ClubCaddieAutomationError("Booking modal did not open.")

    def _wait_for_booking_modal_after_click(self, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                self._current_booking_modal()
                return True
            except ClubCaddieAutomationError:
                time.sleep(0.25)
        return False

    def _fill_booking_modal(self, booking: TeeSheetBookingRequest) -> None:
        modal = self._current_booking_modal()
        self._focus_window(modal)
        self._set_booking_when_and_where(modal, booking)
        self._select_booking_option(modal, "Select Holes", str(booking.holes))
        self._select_booking_option(modal, "Select Players", str(len(booking.players)))
        self._set_booking_when_and_where(modal, booking)
        self._verify_booking_holes(modal, booking.holes)
        self._fill_booking_player_rows(modal, booking)

    def _set_booking_when_and_where(self, modal: object, booking: TeeSheetBookingRequest) -> None:
        target_time = self._normalize_time(booking.time)
        when_edit = self._wait_for_control_near_label(modal, "When", "Edit")
        if when_edit is None:
            raise ClubCaddieAutomationError("Booking modal When field was not found.")
        self._replace_edit_text(when_edit, target_time)

        where_value = "Front" if booking.side == "front" else "Back"
        where_combo = self._wait_for_control_near_label(modal, "Where", "ComboBox")
        if where_combo is None:
            raise ClubCaddieAutomationError("Booking modal Where field was not found.")
        self._select_combo_value(where_combo, where_value)

        self._verify_booking_when_and_where(modal, target_time, where_value)

    def _replace_edit_text(self, edit_control: object, value: str) -> None:
        try:
            edit_control.click_input()
            edit_control.type_keys("^a{BACKSPACE}" + value, with_spaces=True, set_foreground=True)
        except Exception:
            self._set_edit_text(edit_control, value)

    def _select_combo_value(self, combo_box: object, value: str) -> None:
        try:
            combo_box.select(value)
            return
        except Exception:
            pass

        try:
            combo_box.click_input()
            combo_box.type_keys("^a{BACKSPACE}" + value + "{ENTER}", with_spaces=True, set_foreground=True)
        except Exception:
            self._set_edit_text(combo_box, value)

    def _verify_booking_when_and_where(self, modal: object, target_time: str, where_value: str) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            when_edit = self._control_near_label(modal, "When", "Edit")
            where_combo = self._control_near_label(modal, "Where", "ComboBox")
            when_text = self._text_of(when_edit) if when_edit is not None else ""
            where_text = self._combo_text(where_combo) if where_combo is not None else ""
            if self._normalize_time_or_none(when_text) == target_time and where_value.lower() in where_text.lower():
                return
            time.sleep(0.2)

        raise ClubCaddieAutomationError(
            f"Booking modal stayed at {when_text or 'unknown time'} / {where_text or 'unknown side'} "
            f"instead of {target_time} / {where_value}."
        )

    def _verify_booking_holes(self, modal: object, holes: int) -> None:
        if self._is_option_selected_near_label(modal, "Select Holes", str(holes)):
            return

        expected_class_prefix = "18 " if holes == 18 else "9 "
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            class_texts = [
                self._text_of(control)
                for control in self._descendants(modal)
                if self._is_visible(control)
                and self._safe_rectangle(control) is not None
                and "ride" in self._text_of(control).lower()
            ]
            if any(text.strip().lower().startswith(expected_class_prefix) for text in class_texts):
                return
            time.sleep(0.2)

        raise ClubCaddieAutomationError(f"Booking modal did not apply {holes} holes.")

    def _is_option_selected_near_label(self, modal: object, label_text: str, option_text: str) -> bool:
        label_rect = self._first_text_rectangle(modal, [rf"^{re.escape(label_text)}$"])
        if label_rect is None:
            return False

        label_center_y = (label_rect.top + label_rect.bottom) // 2
        for control in self._descendants(modal):
            if not self._is_visible(control) or self._text_of(control) != option_text:
                continue
            rectangle = self._safe_rectangle(control)
            if rectangle is None or not self._has_size(rectangle):
                continue
            control_center_y = (rectangle.top + rectangle.bottom) // 2
            if abs(control_center_y - label_center_y) > 35 or rectangle.left < label_rect.right:
                continue
            if self._is_selected(control):
                return True

        return False

    def _is_selected(self, control: object) -> bool:
        try:
            return bool(control.get_toggle_state())
        except Exception:
            pass

        try:
            return bool(control.iface_selection_item.CurrentIsSelected)
        except Exception:
            pass

        try:
            return "checked" in str(control.legacy_properties().get("State", "")).lower()
        except Exception:
            return False

    def _wait_for_control_near_label(
        self,
        root_control: object,
        label_text: str,
        control_type: str,
        timeout_seconds: int = 5,
    ) -> object | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            control = self._control_near_label(root_control, label_text, control_type)
            if control is not None:
                return control
            time.sleep(0.2)
        return None

    def _control_near_label(
        self,
        root_control: object,
        label_text: str,
        control_type: str,
    ) -> object | None:
        candidates = []
        label_rectangles = [
            rectangle
            for control in self._descendants(root_control)
            if self._is_visible(control)
            and self._text_matches(control, [rf"^{re.escape(label_text)}$"])
            and (rectangle := self._safe_rectangle(control)) is not None
            and self._has_size(rectangle)
        ]
        if not label_rectangles:
            return None

        controls = [
            control
            for control in self._descendants(root_control, control_type=control_type)
            if self._is_actionable(control)
        ]
        for label_rect in label_rectangles:
            label_center_y = (label_rect.top + label_rect.bottom) // 2
            for control in controls:
                rectangle = self._safe_rectangle(control)
                if rectangle is None or not self._has_size(rectangle):
                    continue
                if rectangle.left + 5 < label_rect.right:
                    continue
                control_center_y = (rectangle.top + rectangle.bottom) // 2
                y_distance = abs(control_center_y - label_center_y)
                if y_distance <= 35:
                    x_distance = abs(rectangle.left - label_rect.right)
                    candidates.append((y_distance, x_distance, rectangle.left, control))

        if not candidates:
            return None

        _, _, _, control = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
        return control

    def _has_size(self, rectangle: object) -> bool:
        return rectangle.right > rectangle.left and rectangle.bottom > rectangle.top

    def _combo_text(self, combo_box: object) -> str:
        try:
            return str(combo_box.selected_text())
        except Exception:
            pass

        try:
            legacy_value = combo_box.legacy_properties().get("Value")
            if legacy_value:
                return str(legacy_value)
        except Exception:
            pass

        return self._text_of(combo_box)

    def _current_booking_modal(self) -> object:
        for window in self._visible_windows(app_only=True):
            combined = "\n".join(self._control_texts(window)).upper()
            if "RESERVE" in combined and "SELECT PLAYERS" in combined and "SELECT HOLES" in combined:
                return window
        raise ClubCaddieAutomationError("Booking modal is not visible.")

    def _select_booking_option(self, modal: object, label_text: str, option_text: str) -> None:
        if not self._click_option_near_label(modal, label_text, option_text):
            raise ClubCaddieAutomationError(f"Could not select {label_text}: {option_text}")
        time.sleep(0.3)

    def _click_option_near_label(self, modal: object, label_text: str, option_text: str) -> bool:
        label_rect = self._first_text_rectangle(modal, [rf"^{re.escape(label_text)}$"])
        if label_rect is None:
            return self._click_first_match(modal, [rf"^{re.escape(option_text)}$"])

        candidates = []
        for control in self._descendants(modal):
            if not self._is_visible(control) or self._text_of(control) != option_text:
                continue
            rectangle = self._safe_rectangle(control)
            if rectangle is None or not self._has_size(rectangle):
                continue
            if abs(((rectangle.top + rectangle.bottom) // 2) - ((label_rect.top + label_rect.bottom) // 2)) <= 35:
                candidates.append((rectangle.left, control))

        if not candidates:
            return False

        _, control = sorted(candidates, key=lambda item: item[0])[0]
        self._invoke_or_click(control)
        return True

    def _first_text_rectangle(self, root_control: object, text_patterns: Iterable[str]) -> object | None:
        for control in self._descendants(root_control):
            if self._is_visible(control) and self._text_matches(control, text_patterns):
                rectangle = self._safe_rectangle(control)
                if rectangle is not None and self._has_size(rectangle):
                    return rectangle
        return None

    def _fill_booking_player_rows(self, modal: object, booking: TeeSheetBookingRequest) -> None:
        row_edits = self._booking_row_edits(modal)
        if len(row_edits) < len(booking.players):
            raise ClubCaddieAutomationError("Not enough visible booking player rows were found.")

        for player_index, player in enumerate(booking.players):
            columns = row_edits[player_index]
            self._set_edit_text(columns[0], player.last_name)
            self._set_edit_text(columns[1], player.first_name)
            if player.email and len(columns) > 3:
                self._set_edit_text(columns[3], str(player.email))
            if player.mobile_number and len(columns) > 4:
                self._set_edit_text(columns[4], player.mobile_number)

    def _booking_row_edits(self, modal: object) -> list[list[object]]:
        data_items = [
            item
            for item in self._descendants(modal, control_type="DataItem")
            if self._is_visible(item)
            and "PlayerDetail" in self._text_of(item)
        ]
        rows: list[list[object]] = []

        for data_item in sorted(data_items, key=lambda item: self._safe_rectangle(item).top):
            row_rectangle = self._safe_rectangle(data_item)
            if row_rectangle is None:
                continue

            edits = []
            for edit in self._descendants(data_item, control_type="Edit"):
                edit_rectangle = self._safe_rectangle(edit)
                if (
                    self._is_actionable(edit)
                    and edit_rectangle is not None
                    and edit_rectangle.top < row_rectangle.top + 35
                ):
                    edits.append(edit)
            edits.sort(key=lambda edit: self._safe_rectangle(edit).left)
            if len(edits) >= 5:
                rows.append(edits)

        return rows

    def _submit_booking_modal(self, booking: TeeSheetBookingRequest) -> None:
        modal = self._current_booking_modal()
        target_time = self._normalize_time(booking.time)
        where_value = "Front" if booking.side == "front" else "Back"
        self._verify_booking_when_and_where(modal, target_time, where_value)
        self._verify_booking_holes(modal, booking.holes)
        if not self._click_first_match(modal, [r"^Reserve$"]):
            raise ClubCaddieAutomationError("Reserve button was not found.")
        self._wait_for_booking_modal_closed(booking)

    def _wait_for_booking_modal_closed(self, booking: TeeSheetBookingRequest) -> None:
        deadline = time.monotonic() + self.settings.window_wait_seconds
        while time.monotonic() < deadline:
            try:
                self._current_booking_modal()
            except ClubCaddieAutomationError:
                return
            time.sleep(0.5)
        raise ClubCaddieAutomationError("Reservation modal did not close after Reserve.")

    def _close_booking_modal_if_present(self) -> None:
        try:
            modal = self._current_booking_modal()
        except ClubCaddieAutomationError:
            return
        self._click_first_match(modal, [r"^OK$"])
        time.sleep(0.2)
        self._click_first_match(modal, [r"^Cancel$"])
        time.sleep(0.5)

    def _booking_response(
        self,
        booking: TeeSheetBookingRequest,
        status: BookingStatus,
        message: str,
        warnings: list[str],
    ) -> TeeSheetBookingResponse:
        return TeeSheetBookingResponse(
            date=booking.date,
            side=booking.side,
            time=self._normalize_time(booking.time),
            holes=booking.holes,
            player_count=len(booking.players),
            status=status,
            message=message,
            warnings=warnings,
        )

    def _balance_side_slots(
        self,
        front: list[TeeSlot],
        back: list[TeeSlot],
    ) -> tuple[list[TeeSlot], list[TeeSlot], int]:
        front_by_time = {slot.time: slot for slot in front}
        back_by_time = {slot.time: slot for slot in back}
        all_times = sorted(
            set(front_by_time) | set(back_by_time),
            key=self._time_sort_key,
        )
        inferred_count = 0

        for time_label in all_times:
            if time_label not in front_by_time:
                front_by_time[time_label] = self._inferred_slot(time_label, "front")
                inferred_count += 1
            if time_label not in back_by_time:
                back_by_time[time_label] = self._inferred_slot(time_label, "back")
                inferred_count += 1

        return (
            self._sort_slots(front_by_time.values()),
            self._sort_slots(back_by_time.values()),
            inferred_count,
        )

    def _inferred_slot(self, time_label: str, side: str) -> TeeSlot:
        return TeeSlot(
            time=time_label,
            side=side,
            status=SlotStatus.UNKNOWN,
            raw_text="Time label inferred from opposite Tee Sheet side; side-specific OCR text was not found.",
            player_or_note=None,
            source="screenshot",
            confidence=0.2,
        )

    def _extract_slots_from_screenshot(self, tee_window: object) -> tuple[list[TeeSlot], list[TeeSlot]]:
        self._focus_window(tee_window)
        self._reset_tee_sheet_scroll(tee_window)
        front: dict[str, TeeSlot] = {}
        back: dict[str, TeeSlot] = {}
        seen_signatures: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        repeated_pages = 0

        for _ in range(90):
            visible_front, visible_back = self._extract_visible_screenshot_slots(tee_window)
            signature = (
                tuple(slot.time for slot in visible_front),
                tuple(slot.time for slot in visible_back),
            )

            new_slots = self._merge_slots(front, visible_front)
            new_slots += self._merge_slots(back, visible_back)

            if signature in seen_signatures or new_slots == 0:
                repeated_pages += 1
            else:
                repeated_pages = 0
                seen_signatures.add(signature)

            if repeated_pages >= 5:
                break

            self._scroll_tee_sheet_down(tee_window)

        return self._sort_slots(front.values()), self._sort_slots(back.values())

    def _extract_visible_screenshot_slots(
        self,
        tee_window: object,
    ) -> tuple[list[TeeSlot], list[TeeSlot]]:
        time.sleep(0.35)
        image = tee_window.capture_as_image()
        width, height = image.size
        grid_top = int(height * 0.24)
        grid_bottom = int(height * 0.97)
        midpoint = width // 2

        full_grid_image = image.crop((0, int(height * 0.20), width, grid_bottom))
        full_front, full_back = self._ocr_slots_from_full_grid(full_grid_image)
        front_image = image.crop((0, grid_top, midpoint, grid_bottom))
        back_image = image.crop((midpoint, grid_top, width, grid_bottom))
        front = {slot.time: slot for slot in full_front}
        back = {slot.time: slot for slot in full_back}

        self._merge_slots(front, self._ocr_slots_for_region(front_image, "front"))
        self._merge_slots(back, self._ocr_slots_for_region(back_image, "back"))

        return self._sort_slots(front.values()), self._sort_slots(back.values())

    def _merge_slots(self, target: dict[str, TeeSlot], slots: Iterable[TeeSlot]) -> int:
        new_slots = 0
        for slot in slots:
            existing = target.get(slot.time)
            if existing is None or self._slot_quality(slot) > self._slot_quality(existing):
                if existing is None:
                    new_slots += 1
                target[slot.time] = slot
        return new_slots

    def _slot_quality(self, slot: TeeSlot) -> tuple[int, float]:
        has_note = 1 if slot.player_or_note else 0
        is_booked = 1 if slot.status in {SlotStatus.BOOKED, SlotStatus.BLOCKED} else 0
        return has_note + is_booked, slot.confidence

    def _sort_slots(self, slots: Iterable[TeeSlot]) -> list[TeeSlot]:
        return sorted(slots, key=lambda slot: self._time_sort_key(slot.time))

    def _time_sort_key(self, time_label: str) -> tuple[int, int]:
        parsed = datetime.strptime(time_label, "%I:%M %p")
        return parsed.hour, parsed.minute

    def _reset_tee_sheet_scroll(self, tee_window: object) -> None:
        for _ in range(8):
            self._scroll_tee_sheet(tee_window, wheel_dist=8)
            time.sleep(0.05)
        time.sleep(0.5)

    def _scroll_tee_sheet_down(self, tee_window: object) -> None:
        self._scroll_tee_sheet(tee_window, wheel_dist=-1)
        time.sleep(0.5)

    def _scroll_tee_sheet(self, tee_window: object, wheel_dist: int) -> None:
        rectangle = self._safe_rectangle(tee_window)
        if rectangle is None:
            raise ClubCaddieAutomationError("Tee Sheet window position was not available.")

        y = int(rectangle.top + (rectangle.bottom - rectangle.top) * 0.68)
        front_x = int(rectangle.left + (rectangle.right - rectangle.left) * 0.25)
        back_x = int(rectangle.left + (rectangle.right - rectangle.left) * 0.75)
        mouse.scroll(coords=(front_x, y), wheel_dist=wheel_dist)
        mouse.scroll(coords=(back_x, y), wheel_dist=wheel_dist)

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

    def _window_contains_any_text(self, window: object, expected_texts: Iterable[str]) -> bool:
        combined = "\n".join(self._control_texts(window)).upper()
        return any(expected.upper() in combined for expected in expected_texts)

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
