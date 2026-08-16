from app.utils import compute_text_delta

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
