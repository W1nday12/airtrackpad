import sys
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config(); tok = cfg["jwt_token"]
u = g.get_user(tok, g.jwt_subject(tok))
fl, ll = u.get("fromLanguage"), u.get("learningLanguage")
st = g.list_stories(tok, fl, ll)
print("course", fl, "->", ll, "| stories:", len(st))
print("cheapest:", st[:3])
