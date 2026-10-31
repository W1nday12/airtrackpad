import sys
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config()
tok = cfg["jwt_token"]
u = g.get_user(tok, g.jwt_subject(tok))
print("from/learn:", u.get("fromLanguage"), "->", u.get("learningLanguage"))
slugs = g.list_stories(tok, u.get("fromLanguage") or "ru", u.get("learningLanguage") or "en")
print("stories found:", len(slugs))
print("first 5:", slugs[:5])
