"""Match: score jobs against your resume(s) and pick which resume to send.

- resume.py  reads .tex or .txt resumes and strips LaTeX into plain text
- skills.py  a vocabulary of skills and the patterns that detect them
- scorer.py  the baseline score (skill coverage + TF-IDF similarity) with an explanation
- llm.py     optional Claude rerank: 0-100 score + a two-sentence reason
"""
