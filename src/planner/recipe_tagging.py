"""Single source of truth for veggie + protein tagging. No side effects on import."""
import re

VEG = {
 "scallion":   r"\b(?:scallions?|green onions?|spring onions?)\b",
 "onion":      r"(?<!green )(?<!spring )\bonions?\b(?! powder)",
 "shallot":    r"\bshallots?\b",
 "garlic":     r"\bgarlic\b(?! powder| salt)",
 "leek":       r"\bleeks?\b",
 "carrot":     r"\bcarrots?\b",
 "cucumber":   r"\bcucumbers?\b",
 "turnip":     r"\bturnips?\b",
 "radish":     r"(?<!horse)\bradish(?:es)?\b",
 "tomato":     r"\b(?:cherry |grape |heirloom |plum |roma )?tomato(?:es)?\b(?! paste| sauce| pur| juice| powder)",
 "bok choy":   r"\b(?:bok|pak)\s*cho[iy]\b",
 "zucchini":   r"\b(?:zucchini|courgettes?|summer squash)\b",
 "winter squash": r"\b(?:butternut|acorn|delicata|kabocha|spaghetti squash|winter squash)\b",
 "kale":       r"\bkale\b",
 "chard":      r"\b(?:swiss |rainbow )?chard\b",
 "spinach":    r"\bspinach\b",
 "collard":    r"\bcollards?\b",
 "cabbage":    r"\b(?:napa |savoy |green |red )?cabbage\b",
 "broccoli":   r"\bbroccoli(?:ni)?\b|\bbroccoli rabe\b",
 "cauliflower":r"\bcauliflower\b",
 "beet":       r"\bbeets?\b|\bbeetroot\b",
 "green bean": r"\b(?:green beans?|string beans?|haricot ?vert)\b",
 "pea":        r"\b(?:snap peas?|snow peas?|sugar snap|green peas?|english peas?|peas)\b",
 "bell pepper":r"\b(?:bell pepper|sweet pepper)s?\b|\b(?:red|green|yellow|orange) bell peppers?\b",
 "chile":      r"\b(?:jalape|poblano|serrano|anaheim|habanero|fresno|thai chil)\w*",
 "eggplant":   r"\b(?:eggplants?|aubergines?)\b",
 "potato":     r"(?<!sweet )\bpotato(?:es)?\b",
 "sweet potato":r"\b(?:sweet potato(?:es)?|yams?)\b",
 "corn":       r"\bcorn\b(?! ?starch| syrup| ?flour| tortilla| chips?| ?meal)",
 "celery":     r"\bcelery\b(?! seed| salt)",
 "fennel":     r"\bfennel\b(?! seed)",
 "parsnip":    r"\bparsnips?\b",
 "kohlrabi":   r"\bkohlrabi\b",
 "brussels sprout": r"\bbrussels? sprouts?\b",
 "asparagus":  r"\basparagus\b",
 "mushroom":   r"\bmushrooms?\b|\b(?:cremini|shiitake|portobello|porcini)\b",
}
PROTEIN = {
 "beef":    r"\b(?:beef|steak|chuck|brisket|short ribs?|ribeye|sirloin|flank|ground beef)\b",
 "chicken": r"\bchickens?\b",
 "turkey":  r"\bturkey\b",
 "lamb":    r"\blamb\b",
 "pork":    r"\b(?:pork|sausage|chorizo|ham|prosciutto|pancetta|bacon|salt pork)\b",
 "fish":    r"\b(?:salmon|cod|tuna|halibut|trout|tilapia|snapper|sea bass|mackerel|sardine|anchov)\w*",
 "seafood": r"\b(?:shrimp|prawns?|scallops?|crab|lobster|clams?|mussels?|squid|calamari|octopus)\b",
 "tofu":    r"\b(?:tofu|tempeh|seitan)\b",
}
FLAVORING = {"pork"}
MEATS = {"beef","chicken","pork","fish","seafood","lamb","turkey"}
PASTA = re.compile(r"\b(?:pasta|spaghetti|linguine|fettuccine|penne|rigatoni|macaroni|noodles?|lasagn|orzo|gnocchi|tagliatelle|bucatini|farfalle|ravioli|tortellini)\b")

_VEG_C = {k: re.compile(v) for k, v in VEG.items()}
_PROT_C = {k: re.compile(v) for k, v in PROTEIN.items()}
# broth/stock is a pantry liquid, not the dish's protein: strip these phrases
# (incl. lists like "vegetable, chicken or turkey broth") before protein detection
_BROTH = re.compile(
    r"\b(?:(?:chicken|beef|turkey|vegetable|veggie|fish|pork|bone|low-sodium|"
    r"reduced-sodium|no-salt-added|homemade|store-bought)[ ,]+(?:or +)?)+"
    r"(?:broth|stock|bouillon|base)\b")

# dish_type: distinguish a standalone dinner ("main") from an accompaniment ("side").
# Anything with a real protein or pasta is a main. The hard cases are vegetarian,
# non-pasta dishes, decided by title/ingredient signals.
_MAIN_SIGNAL = re.compile(
    r"\b(?:soup|stew|chili|chilli|curry|risotto|paella|biryani|pilaf|jambalaya|"
    r"fried rice|ramen|pho|laksa|pad thai|lo mein|chow mein|dal|dahl|daal|"
    r"shakshuka|frittata|quiche|strata|omelet|omelette|casserole|pot pie|"
    r"enchilada|taco|burrito|quesadilla|tostada|pizza|sandwich|burger|sub|gyro|"
    r"bowl|chickpea|lentil|tofu|tempeh|dumpling|pierogi|polenta|congee|"
    r"hash|tart|galette|flatbread|baked? potato|stuffed|gratin|bake\b)\b")
_SIDE_SIGNAL = re.compile(
    r"\b(?:roast(?:ed)?|glazed|creamed|saut[eé]ed|braised|steamed|blistered|"
    r"charred|grilled|smashed|mashed|scalloped|slaw|pickled|marinated|buttered|"
    r"honey|maple|crispy|wedges|fries|chips|salad)\b")
_HEARTY = re.compile(  # a substantial base => likely a main even if vegetarian
    r"\b(?:rice|noodles?|pasta|beans?|lentils?|chickpeas?|quinoa|farro|barley|"
    r"bulgur|couscous|tortillas?|bread|eggs?|gnocchi|orzo|polenta)\b")

def _dish_type(rec, protein, is_pasta, veggies):
    if protein != "vegetarian" or is_pasta:
        return "main"
    blob = " ; ".join(rec.get("ingredients", [])).lower() + " " + rec.get("title", "").lower()
    title = rec.get("title", "").lower()
    if _MAIN_SIGNAL.search(title) or _MAIN_SIGNAL.search(blob):
        return "main"
    if _SIDE_SIGNAL.search(title):
        return "side"
    # fallback: a hearty base => main; a short veg-centric prep => side
    if _HEARTY.search(blob):
        return "main"
    return "side" if len(rec.get("ingredients", [])) <= 8 else "main"

def tag(rec):
    blob = " ; ".join(rec.get("ingredients", [])).lower() + " " + rec.get("title", "").lower()
    veggies = sorted({c for c, pat in _VEG_C.items() if pat.search(blob)})
    pblob = _BROTH.sub(" ", blob)                      # ignore broth when finding protein
    found = [p for p, pat in _PROT_C.items() if pat.search(pblob)]
    mains = [p for p in found if p not in FLAVORING]
    protein = mains[0] if mains else (found[0] if found else "vegetarian")
    is_pasta = bool(PASTA.search(blob))
    return {"veggies": veggies, "protein": protein, "is_pasta": is_pasta,
            "dish_type": _dish_type(rec, protein, is_pasta, veggies)}
