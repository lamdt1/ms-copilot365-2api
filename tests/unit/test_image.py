"""Unit tests for app/substrate/image.py"""
import pytest
from app.substrate.image import should_generate_image, classify_image_failure


class TestShouldGenerateImage:
    def test_keyword_draw(self):
        assert should_generate_image("please draw a cat", None) is True

    def test_keyword_ve(self):
        assert should_generate_image("Vẽ một con chó", None) is True

    def test_keyword_tao_anh(self):
        assert should_generate_image("hãy tạo ảnh con mèo", None) is True

    def test_keyword_generate_image(self):
        assert should_generate_image("generate image of sunset", None) is True

    def test_keyword_create_image(self):
        assert should_generate_image("create image of a house", None) is True

    def test_no_match(self):
        assert should_generate_image("explain quantum physics", None) is False

    def test_tool_with_image_name(self):
        tools = [{"function": {"name": "generateImage", "parameters": {}}}]
        assert should_generate_image("do something", tools) is True

    def test_tool_without_image_name(self):
        tools = [{"function": {"name": "bash", "parameters": {}}}]
        assert should_generate_image("do something", tools) is False


class TestClassifyImageFailure:
    def test_quota_exceeded(self):
        assert classify_image_failure("I can't generate any more images today") == "quota_exceeded"

    def test_quota_limit(self):
        assert classify_image_failure("You've reached your image limit for today.") == "quota_exceeded"

    def test_capacity(self):
        assert classify_image_failure("I'm having trouble creating image right now") == "capacity"

    def test_capacity_later(self):
        assert classify_image_failure("Sorry, please try again later.") == "capacity"

    def test_content_filtered(self):
        assert classify_image_failure("That request is against policy.") == "content_filtered"

    def test_content_filtered_unable(self):
        assert classify_image_failure("I'm unable to create that image.") == "content_filtered"

    def test_no_image(self):
        assert classify_image_failure("Here is a normal text response.") == "no_image"

    def test_empty(self):
        assert classify_image_failure("") == "no_image"
