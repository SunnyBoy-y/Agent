import os
import sys
import unittest
import types

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "langchain_core.tools" not in sys.modules:
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

if "src.utils.rag_helper" not in sys.modules:
    rag_module = types.ModuleType("src.utils.rag_helper")
    class _RAGHelper:
        pass
    rag_module.RAGHelper = _RAGHelper
    sys.modules["src.utils.rag_helper"] = rag_module

from src.tools.professional_skills import ProfessionalSkills


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

if __name__ == "__main__":
    unittest.main()
