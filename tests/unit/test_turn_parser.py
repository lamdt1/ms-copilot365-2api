import json
from app.substrate.turn_parser import TurnParser, RECORD_SEPARATOR


def test_turn_parser_handshake():
    parser = TurnParser()
    events = list(parser.feed(f"{{}}{RECORD_SEPARATOR}"))
    assert len(events) == 1
    assert events[0][0] == "handshake"


def test_turn_parser_ping():
    parser = TurnParser()
    events = list(parser.feed(f'{{"type":6}}{RECORD_SEPARATOR}'))
    assert len(events) == 1
    assert events[0][0] == "ping"


def test_turn_parser_done():
    parser = TurnParser()
    events = list(parser.feed(f'{{"type":3,"result":{{"conversationMessages": 5}}}}{RECORD_SEPARATOR}'))
    assert len(events) == 1
    assert events[0][0] == "done"
    assert events[0][1]["conversationMessages"] == 5


def test_turn_parser_update_text():
    parser = TurnParser()
    payload = {
        "type": 1,
        "target": "update",
        "arguments": [{
            "writeAtCursor": "Hello world"
        }]
    }
    msg = json.dumps(payload) + RECORD_SEPARATOR
    events = list(parser.feed(msg))
    assert len(events) == 1
    assert events[0][0] == "text"
    assert events[0][1]["text"] == "Hello world"


def test_turn_parser_disengaged():
    parser = TurnParser()
    payload = {
        "type": 1,
        "target": "update",
        "arguments": [{
            "messageType": "Disengaged",
            "text": "Sorry, I can't talk about this.",
            "deaScore": 0.85
        }]
    }
    msg = json.dumps(payload) + RECORD_SEPARATOR
    events = list(parser.feed(msg))
    assert len(events) == 1
    assert events[0][0] == "disengaged"
    assert "Sorry" in events[0][1]["reason"]
    assert events[0][1]["dea_score"] == 0.85


def test_turn_parser_multiple_frames_in_chunk():
    parser = TurnParser()
    payload1 = {"type": 6}
    payload2 = {"type": 3, "result": {}}

    # 2 frames bundled in 1 TCP read chunk
    chunk = json.dumps(payload1) + RECORD_SEPARATOR + json.dumps(payload2) + RECORD_SEPARATOR

    events = list(parser.feed(chunk))
    assert len(events) == 2
    assert events[0][0] == "ping"
    assert events[1][0] == "done"


def test_turn_parser_type_7_error():
    parser = TurnParser()
    events = list(parser.feed(f'{{"type":7,"error":"Connection closed with an error.","allowReconnect":true}}{RECORD_SEPARATOR}'))
    assert len(events) == 1
    assert events[0][0] == "error"
    assert events[0][1]["message"] == "Connection closed with an error."
