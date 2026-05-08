from datetime import date

import pytest

from app.automation.club_caddie import ClubCaddieAutomation, ClubCaddieAutomationError
from app.schemas import SlotStatus, TeeSlot


class FakeRectangle:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeControl:
    def __init__(
        self,
        text: str = "",
        visible: bool = True,
        enabled: bool = True,
        children: list["FakeControl"] | None = None,
        control_type: str | None = None,
        rectangle: FakeRectangle | None = None,
        selected: bool = False,
    ) -> None:
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.children = children or []
        self.control_type = control_type
        self._rectangle = rectangle
        self.selected = selected
        self.clicked = False

    def window_text(self) -> str:
        return self.text

    def descendants(self, control_type: str | None = None) -> list["FakeControl"]:
        descendants: list[FakeControl] = []
        nodes = list(self.children)
        while nodes:
            node = nodes.pop(0)
            if control_type is None or node.control_type == control_type:
                descendants.append(node)
            nodes.extend(node.children)
        return descendants

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return self.enabled

    def rectangle(self) -> FakeRectangle:
        if self._rectangle is None:
            raise RuntimeError("No rectangle configured.")
        return self._rectangle

    def get_toggle_state(self) -> int:
        return int(self.selected)

    def invoke(self) -> None:
        self.clicked = True


def automation_without_desktop() -> ClubCaddieAutomation:
    return ClubCaddieAutomation.__new__(ClubCaddieAutomation)


def test_classifies_add_only_slot_as_available() -> None:
    automation = automation_without_desktop()

    status = automation._classify_slot("6:44 AM | Add")

    assert status == SlotStatus.AVAILABLE


def test_classifies_named_slot_as_booked() -> None:
    automation = automation_without_desktop()

    status = automation._classify_slot("4:44 PM | Example Player")
    note = automation._extract_note("4:44 PM | Example Player", "4:44 PM")

    assert status == SlotStatus.BOOKED
    assert note == "Example Player"


def test_ignores_ocr_artifacts_from_add_buttons() -> None:
    automation = automation_without_desktop()

    status = automation._classify_slot("5:40 pm (ECE | corEaMy Add |")
    note = automation._extract_note("5:40 pm (ECE | corEaMy Add |", "5:40 PM")

    assert status == SlotStatus.AVAILABLE
    assert note is None


def test_ocr_row_boundary_stops_at_malformed_time() -> None:
    automation = automation_without_desktop()

    text = automation._text_until_next_time(["5:24 em ECE", "Add"])

    assert text == ""


def test_hidden_controls_are_not_used_for_detection_or_clicks() -> None:
    automation = automation_without_desktop()
    hidden_login = FakeControl("LOGIN", visible=False)
    visible_checkin = FakeControl("Check-in")
    window = FakeControl("Rolling Meadows Golf Club", children=[hidden_login, visible_checkin])

    assert "LOGIN" not in automation._control_texts(window)
    assert automation._click_first_match(window, [r"^LOGIN$"]) is False
    assert hidden_login.clicked is False


def test_balances_missing_side_slots_as_unknown() -> None:
    automation = automation_without_desktop()

    front, back, inferred_count = automation._balance_side_slots(
        front=[
            TeeSlot(
                time="8:36 AM",
                side="front",
                status=SlotStatus.AVAILABLE,
                raw_text="8:36 AM | Add",
            )
        ],
        back=[],
    )

    assert inferred_count == 1
    assert front[0].time == back[0].time == "8:36 AM"
    assert back[0].status == SlotStatus.UNKNOWN


def test_normalizes_time_labels() -> None:
    automation = automation_without_desktop()

    assert automation._normalize_time("06:44am") == "6:44 AM"


def test_regional_24_hour_times_match_booking_times() -> None:
    automation = automation_without_desktop()

    assert automation._time_text_matches_target("08:20", "8:20 AM")
    assert automation._time_text_matches_target("17:40", "5:40 PM")
    assert not automation._time_text_matches_target("08:20", "8:20 PM")


def test_turn_time_zero_accepts_24_hour_and_midnight_text() -> None:
    automation = automation_without_desktop()

    assert automation._turn_time_is_zero("00:00")
    assert automation._turn_time_is_zero("12:00 AM")
    assert not automation._turn_time_is_zero("09:00")


def test_image_coords_are_translated_to_screen_coords() -> None:
    automation = automation_without_desktop()
    window = FakeControl(rectangle=FakeRectangle(-33, 50, 2527, 1442))

    assert automation._image_coords_to_screen(window, (1384, 463)) == (1351, 513)


def test_time_only_ocr_row_clicks_target_row() -> None:
    automation = automation_without_desktop()
    row = [
        {"text": "5:40", "center_x": 1317.2, "center_y": 842.5},
        {"text": "PM", "center_x": 1344.0, "center_y": 842.2},
    ]

    assert automation._time_row_fallback_coords(row, "back", 2560) == (1397, 842)


def test_parses_multiple_visible_date_formats() -> None:
    automation = automation_without_desktop()

    assert automation._parse_date_text("5/8/2026") == date(2026, 5, 8)
    assert automation._parse_date_text("08-May-2026") == date(2026, 5, 8)
    assert automation._parse_date_text("Friday, May 08, 2026") == date(2026, 5, 8)
    assert automation._parse_date_text("10-06-2026") == date(2026, 6, 10)
    assert automation._parse_date_text("16-10-2026") == date(2026, 10, 16)


def test_formats_target_date_like_visible_field() -> None:
    automation = automation_without_desktop()
    target_date = date(2026, 10, 6)

    assert automation._format_date_for_visible_field(target_date, "5/8/2026") == "10/6/2026"
    assert automation._format_date_for_visible_field(target_date, "08-05-2026") == "06-10-2026"
    assert automation._format_date_for_visible_field(target_date, "08-May-2026") == "06-Oct-2026"


def test_calendar_day_labels_include_ec2_day_first_format() -> None:
    automation = automation_without_desktop()

    labels = automation._calendar_day_labels(date(2026, 10, 6))

    assert "Tuesday, October 6, 2026" in labels
    assert "Tuesday, 06 October 2026" in labels
    assert "06 October 2026" in labels


def test_arrow_navigation_rejects_large_date_jumps() -> None:
    automation = automation_without_desktop()
    tee_window = FakeControl()
    date_edit = FakeControl("08-05-2026")

    with pytest.raises(ClubCaddieAutomationError, match="calendar selection is required"):
        automation._navigate_date_with_arrows(tee_window, date_edit, date(2026, 10, 6))


def test_booking_row_edits_ignore_notes_field_below_player_row() -> None:
    automation = automation_without_desktop()
    row = FakeControl(
        text="POSApp.ViewModels.PlayerDetail",
        control_type="DataItem",
        rectangle=FakeRectangle(729, 708, 1829, 788),
        children=[
            FakeControl("Last", control_type="Edit", rectangle=FakeRectangle(884, 717, 974, 733)),
            FakeControl("First", control_type="Edit", rectangle=FakeRectangle(988, 713, 1087, 743)),
            FakeControl("", control_type="Edit", rectangle=FakeRectangle(1098, 714, 1194, 744)),
            FakeControl("email@example.com", control_type="Edit", rectangle=FakeRectangle(1205, 713, 1304, 743)),
            FakeControl("", control_type="Edit", rectangle=FakeRectangle(1314, 713, 1412, 743)),
            FakeControl("", control_type="Edit", rectangle=FakeRectangle(1680, 714, 1710, 744)),
            FakeControl("P1 Notes", control_type="Edit", rectangle=FakeRectangle(1247, 748, 1722, 778)),
        ],
    )
    modal = FakeControl(children=[row])

    row_edits = automation._booking_row_edits(modal)

    assert len(row_edits) == 1
    assert [edit.window_text() for edit in row_edits[0]][:5] == [
        "Last",
        "First",
        "",
        "email@example.com",
        "",
    ]
    assert "P1 Notes" not in [edit.window_text() for edit in row_edits[0]]


def test_control_near_label_targets_matching_row_control() -> None:
    automation = automation_without_desktop()
    hidden_duplicate_where = FakeControl(
        "Where",
        control_type="Text",
        rectangle=FakeRectangle(0, 0, 0, 0),
    )
    background_date = FakeControl(
        "10/5/2026",
        control_type="Edit",
        rectangle=FakeRectangle(78, 243, 164, 265),
    )
    when_label = FakeControl("When", control_type="Text", rectangle=FakeRectangle(48, 110, 88, 130))
    when_edit = FakeControl("7:16 AM", control_type="Edit", rectangle=FakeRectangle(98, 104, 180, 136))
    where_label = FakeControl("Where", control_type="Text", rectangle=FakeRectangle(205, 110, 248, 130))
    where_combo = FakeControl("Front", control_type="ComboBox", rectangle=FakeRectangle(260, 104, 350, 136))
    unrelated_combo = FakeControl("Back", control_type="ComboBox", rectangle=FakeRectangle(260, 300, 350, 332))
    modal = FakeControl(
        children=[
            hidden_duplicate_where,
            background_date,
            when_label,
            when_edit,
            where_label,
            where_combo,
            unrelated_combo,
        ],
    )

    assert automation._control_near_label(modal, "When", "Edit") is when_edit
    assert automation._control_near_label(modal, "Where", "ComboBox") is where_combo


def test_holes_verification_uses_selected_radio_state() -> None:
    automation = automation_without_desktop()
    select_holes_label = FakeControl(
        "Select Holes",
        control_type="Text",
        rectangle=FakeRectangle(729, 480, 805, 518),
    )
    nine = FakeControl(
        "9",
        control_type="RadioButton",
        rectangle=FakeRectangle(810, 484, 845, 514),
        selected=False,
    )
    eighteen = FakeControl(
        "18",
        control_type="RadioButton",
        rectangle=FakeRectangle(850, 484, 885, 514),
        selected=True,
    )
    modal = FakeControl(children=[select_holes_label, nine, eighteen])

    assert automation._is_option_selected_near_label(modal, "Select Holes", "18")
    assert not automation._is_option_selected_near_label(modal, "Select Holes", "9")
