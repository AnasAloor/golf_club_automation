from app.automation.club_caddie import ClubCaddieAutomation
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
