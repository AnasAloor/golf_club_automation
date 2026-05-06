from app.automation.club_caddie import ClubCaddieAutomation
from app.schemas import SlotStatus, TeeSlot


class FakeControl:
    def __init__(
        self,
        text: str = "",
        visible: bool = True,
        enabled: bool = True,
        children: list["FakeControl"] | None = None,
    ) -> None:
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.children = children or []
        self.clicked = False

    def window_text(self) -> str:
        return self.text

    def descendants(self, control_type: str | None = None) -> list["FakeControl"]:
        return self.children

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return self.enabled

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
