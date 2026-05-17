import os
import sys
import unittest
import types
import json
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import langchain_core.tools  # noqa: F401
except Exception:
    tools_module = types.ModuleType("langchain_core.tools")

    def _tool(func=None, *args, **kwargs):
        if callable(func):
            return func
        def decorator(f):
            return f
        return decorator

    tools_module.tool = _tool
    core_module = types.ModuleType("langchain_core")
    core_module.tools = tools_module
    sys.modules["langchain_core"] = core_module
    sys.modules["langchain_core.tools"] = tools_module

try:
    import src.utils.rag_helper  # noqa: F401
except Exception:
    rag_module = types.ModuleType("src.utils.rag_helper")
    class _RAGHelper:
        pass
    rag_module.RAGHelper = _RAGHelper
    sys.modules["src.utils.rag_helper"] = rag_module

from src.tools.professional_skills import ProfessionalSkills


class _FakePhotoSearchResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _invoke_photo_search(keyword, username=None):
    tool = ProfessionalSkills.search_family_photos
    payload = {"keyword": keyword}
    if username is not None:
        payload["username"] = username
    if hasattr(tool, "invoke"):
        return tool.invoke(payload)
    return tool(**payload)


class PhotoKeywordNormalizationTests(unittest.TestCase):
    def test_normalize_photo_keyword_core_cases(self):
        cases = {
            "帮我找孙子的照片": "孙子",
            "找孙子的": "孙子",
            "我想看看老伴的相片": "老伴",
            "麻烦你帮我找一下女儿视频": "女儿",
            "看看照片": "",
        }
        for text, expected in cases.items():
            self.assertEqual(ProfessionalSkills.normalize_photo_keyword(text), expected)

    def test_photo_score_prefers_semantic_description(self):
        semantic_record = {
            "originalFileName": "IMG_0001.jpg",
            "description": "孙女在公园和奶奶合影，手里拿着一束花。",
            "tags": ["孙女", "公园"],
            "people": ["孙女"],
            "location": "人民公园",
        }
        filename_record = {
            "originalFileName": "孙女证件照.jpg",
            "description": "家里拍的一张头像。",
            "tags": ["孙女"],
            "people": ["孙女"],
            "location": "家里",
        }

        semantic_score = ProfessionalSkills.score_photo_record(semantic_record, "孙女在公园")
        filename_score = ProfessionalSkills.score_photo_record(filename_record, "孙女在公园")

        self.assertGreater(semantic_score, filename_score)
        self.assertGreaterEqual(semantic_score, 30.0)

    def test_search_family_photos_uses_semantic_metadata_fallback(self):
        records = [
            {
                "uuid": "wanted",
                "originalFileName": "IMG_0001.jpg",
                "fileType": "image/jpeg",
                "description": "孙女在公园和奶奶合影，手里拿着一束花。",
                "tags": ["孙女", "公园"],
                "people": ["孙女"],
                "location": "人民公园",
                "time_text": "去年春天",
                "caption_source": "family_upload",
            },
            {
                "uuid": "other",
                "originalFileName": "kitchen.jpg",
                "fileType": "image/jpeg",
                "description": "厨房里的一桌晚饭。",
                "tags": ["晚饭"],
                "people": ["女儿"],
                "location": "家里",
            },
        ]
        requested_keywords = []

        def fake_get(_url, params=None, timeout=None):
            keyword = (params or {}).get("keyword", "")
            requested_keywords.append(keyword)
            if keyword:
                return _FakePhotoSearchResponse([])
            return _FakePhotoSearchResponse(records)

        with patch("src.tools.professional_skills.requests.get", side_effect=fake_get):
            result = json.loads(_invoke_photo_search("我想看孙女在公园的照片"))

        self.assertEqual(result["status"], "success")
        self.assertIn("", requested_keywords)
        self.assertEqual(result["photos"][0]["original_file_name"], "IMG_0001.jpg")
        self.assertEqual(result["photos"][0]["desc"], "孙女在公园和奶奶合影，手里拿着一束花。")
        self.assertEqual(result["photos"][0]["people"], ["孙女"])
        self.assertEqual(result["photos"][0]["location"], "人民公园")
        self.assertEqual(result["photos"][0]["time_text"], "去年春天")
        self.assertEqual(result["photos"][0]["caption_source"], "family_upload")
        self.assertTrue(result["photos"][0]["metadata_available"])

    def test_specific_query_with_generic_photo_words_still_filters_by_entity(self):
        records = [
            {
                "uuid": "other",
                "originalFileName": "kitchen.jpg",
                "fileType": "image/jpeg",
                "description": "厨房里的晚饭照片",
                "tags": ["厨房"],
                "people": ["儿子"],
                "location": "家里",
            },
            {
                "uuid": "wanted",
                "originalFileName": "granddaughter.jpg",
                "fileType": "image/jpeg",
                "description": "孙女在公园里笑着拍照",
                "tags": ["孙女", "公园"],
                "people": ["孙女"],
                "location": "公园",
            },
        ]
        requested_keywords = []

        def fake_get(_url, params=None, timeout=None):
            keyword = (params or {}).get("keyword", "")
            requested_keywords.append(keyword)
            if keyword:
                return _FakePhotoSearchResponse([])
            return _FakePhotoSearchResponse(records)

        with patch("src.tools.professional_skills.requests.get", side_effect=fake_get):
            result = json.loads(_invoke_photo_search("看看孙女照片"))

        self.assertEqual(result["status"], "success")
        self.assertIn("孙女", requested_keywords)
        self.assertIn("", requested_keywords)
        self.assertEqual(len(result["photos"]), 1)
        self.assertEqual(result["photos"][0]["original_file_name"], "granddaughter.jpg")

    def test_photo_result_contract_preserves_filename_fallback(self):
        result = ProfessionalSkills.build_photo_result(
            {
                "uuid": "plain",
                "originalFileName": "old_photo.jpg",
                "fileType": "image/jpeg",
            }
        )

        self.assertEqual(result["desc"], "old_photo.jpg")
        self.assertEqual(result["original_file_name"], "old_photo.jpg")
        self.assertFalse(result["metadata_available"])

if __name__ == "__main__":
    unittest.main()
