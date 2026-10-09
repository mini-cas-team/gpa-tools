"""llm-category: rank students on a course category decided by an LLM.

Independent of gpa_ana. Reads the per-student ``*.common.json`` transcripts,
asks a local LLM (Ollama) whether each course belongs to the category, and
writes a ranking CSV in the same layout gpa_ana uses.

Run:  python -m llm_category --category STEM
"""
