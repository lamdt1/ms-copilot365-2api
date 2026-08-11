import json
from app.tools.stream_parser import ToolCallStreamParser


def test_normal_content():
    parser = ToolCallStreamParser()
    events = list(parser.feed("Here is some normal text without tool tags."))
    assert len(events) == 1
    assert events[0][0] == "content"
    assert events[0][1]["text"] == "Here is some normal text without tool tags."


def test_xml_tool_call_perfect_stream():
    parser = ToolCallStreamParser()
    events = []
    # 1. Start chunk
    events.extend(list(parser.feed("Thinking... <tool_call>")))
    # 2. Payload chunk
    events.extend(list(parser.feed('{"name":"read_file", "arguments": {"path":"test.txt"}}')))
    # 3. Close chunk
    events.extend(list(parser.feed("</tool_call> Done.")))

    # Events should be:
    # content: "Thinking... "
    # tool_name: "read_file"
    # tool_args: json str
    # content: " Done."

    assert len(events) == 4
    assert events[0] == ("content", {"text": "Thinking... "})
    assert events[1] == ("tool_name", {"name": "read_file"})
    assert events[2][0] == "tool_args"

    args = json.loads(events[2][1]["arguments"])
    assert args["path"] == "test.txt"

    assert events[3] == ("content", {"text": " Done."})


def test_markdown_bash_codeblock_fallback():
    parser = ToolCallStreamParser()
    events = []

    text = "Sure, I will list the files:\n```bash\nls -la\n```\nLet me know if you need more."
    events.extend(list(parser.feed(text)))

    # Expected:
    # tool_name execute_bash
    # tool_args {"command": "ls -la"}
    # content: before and after code block

    assert events[0] == ("tool_name", {"name": "execute_bash"})
    assert events[1] == ("tool_args", {"arguments": '{"command": "ls -la"}'})
    assert events[2][0] == "content"
    assert "Sure, I will list the files:" in events[2][1]["text"]
    assert events[3][0] == "content"
    assert "Let me know if you need more." in events[3][1]["text"]


def test_partial_xml_tag_buffering():
    parser = ToolCallStreamParser()
    events = []

    # Should buffer '<too' and not emit it yet
    events.extend(list(parser.feed("Text before <too")))
    assert len(events) == 1
    assert events[0] == ("content", {"text": "Text before "})

    # Complete the tag
    events.extend(list(parser.feed('l_call>{"name": "fetch"}</tool_call>')))
    assert len(events) == 3 # tool_name, tool_args
    assert events[1] == ("tool_name", {"name": "fetch"})
    assert events[2][0] == "tool_args"
