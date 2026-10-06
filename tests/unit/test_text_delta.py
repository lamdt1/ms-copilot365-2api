from app.utils import compute_text_delta, _strip_citations

def test_incremental_delta():
    # Setup
    text_buffer = "Hello"
    payload = {"text": " World", "is_full": False}

    # Action
    delta, new_buffer = compute_text_delta(payload, text_buffer)

    # Assert
    assert delta == " World"
    assert new_buffer == "Hello World"

def test_is_full_cumulative_same_start():
    # Setup
    text_buffer = "Hello"
    payload = {"text": "Hello World", "is_full": True}

    # Action
    delta, new_buffer = compute_text_delta(payload, text_buffer)

    # Assert
    assert delta == " World"
    assert new_buffer == "Hello World"

def test_is_full_cumulative_rewritten():
    # Setup
    text_buffer = "Hello"
    payload = {"text": "Hi World", "is_full": True}

    # Action
    delta, new_buffer = compute_text_delta(payload, text_buffer)

    # Assert
    assert delta == "Hi World"
    assert new_buffer == "Hi World"

def test_empty_buffer_incremental():
    payload = {"text": "Start", "is_full": False}
    delta, new_buffer = compute_text_delta(payload, "")
    assert delta == "Start"
    assert new_buffer == "Start"

def test_empty_buffer_is_full():
    payload = {"text": "Start", "is_full": True}
    delta, new_buffer = compute_text_delta(payload, "")
    assert delta == "Start"
    assert new_buffer == "Start"

def test_strip_citations_and_reasoning_lines():
    raw = (
        "Đang sắp xếp mọi thứ…\n"
        "Đang tìm kiếm “Copilot”\n"
        "Copilot said:\n"
        "Dựa trên các công cụ hiện có+1.\n"
        "Kết quả chi tiết citeturn3search31 tại đây."
    )
    cleaned = _strip_citations(raw)
    assert "Đang sắp xếp mọi thứ" not in cleaned
    assert "Đang tìm kiếm" not in cleaned
    assert "Copilot said" not in cleaned
    assert "+1" not in cleaned
    assert "citeturn3search31" not in cleaned
    assert cleaned == "Dựa trên các công cụ hiện có.\nKết quả chi tiết  tại đây."

def test_compute_text_delta_filters_reasoning_and_prevents_duplication():
    # Frame 1: Copilot renders preliminary status + partial text
    payload1 = {
        "text": "Đang sắp xếp mọi thứ…\nĐang tìm kiếm “Copilot”\nDựa trên các công cụ",
        "is_full": True
    }
    delta1, buffer1 = compute_text_delta(payload1, "")
    assert delta1 == "Dựa trên các công cụ"
    assert buffer1 == "Dựa trên các công cụ"

    # Frame 2: Copilot renders more text, still with status
    payload2 = {
        "text": "Đang sắp xếp mọi thứ…\nĐang tìm kiếm “Copilot”\nDựa trên các công cụ hiện có",
        "is_full": True
    }
    delta2, buffer2 = compute_text_delta(payload2, buffer1)
    assert delta2 == " hiện có"
    assert buffer2 == "Dựa trên các công cụ hiện có"

    # Frame 3: Copilot removes status banner from DOM, leaving clean text
    payload3 = {
        "text": "Dựa trên các công cụ hiện có và phát triển",
        "is_full": True
    }
    delta3, buffer3 = compute_text_delta(payload3, buffer2)
    assert delta3 == " và phát triển"
    assert buffer3 == "Dựa trên các công cụ hiện có và phát triển"
