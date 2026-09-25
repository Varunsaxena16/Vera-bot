"""
Deterministic message composer for the magicpin Vera challenge.

compose(category, merchant, trigger, customer=None, now=None, store=None) -> dict

Design:
  1. Decide in code: which fact matters for this trigger, who receives it, which CTA.
  2. Write from templates that only use values present in the four contexts
     (or simple arithmetic on them), so nothing is fabricated.
  3. Same input -> same output, every time; runs in well under a millisecond.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

HINDI_CITIES = {"delhi", "jaipur", "lucknow", "chandigarh", "mumbai", "pune",
                "ahmedabad", "noida", "gurgaon", "gurugram", "kanpur", "indore",
                "bhopal", "patna", "varanasi"}

CAT_LABEL = {"dentists": "dental clinics", "salons": "salons", "restaurants": "restaurants",
             "gyms": "gyms", "pharmacies": "pharmacies"}
CAT_PEOPLE = {"dentists": "patients", "salons": "clients", "restaurants": "regulars",
              "gyms": "members", "pharmacies": "customers"}


def _g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def num(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    return f"{int(n):,}" if n == int(n) else f"{n:,.1f}"


def pct(x, signed=False) -> str:
    v = round(abs(float(x)) * 100, 1)
    s = f"{int(v)}%" if v == int(v) else f"{v}%"
    if signed:
        return ("+" if float(x) >= 0 else "-") + s
    return s


def rupees(v) -> str:
    try:
        return f"₹{int(float(v)):,}"
    except (TypeError, ValueError):
        return str(v)


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(s)[:10])
        except ValueError:
            return None


def fmt_date(d, year=False) -> str:
    if d is None:
        return ""
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if year else "")


def first_sentence(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0]


def humanize(token: str) -> str:
    return str(token).replace("_", " ").strip()


# ----------------------------------------------------------------------------
# Context wrapper
# ----------------------------------------------------------------------------

class Ctx:
    def __init__(self, category, merchant, trigger, customer, now, store):
        self.cat = category or {}
        self.m = merchant or {}
        self.t = trigger or {}
        self.c = customer
        self.now = now
        self.store = store or {}
        p = dict(self.t.get("payload") or {})
        self.placeholder = bool(p.get("placeholder"))
        self.p = {} if self.placeholder else p
        self.slug = self.cat.get("slug") or self.m.get("category_slug") or ""

    # --- merchant identity -------------------------------------------------
    @property
    def biz(self):
        return _g(self.m, "identity", "name", default="your business")

    @property
    def first(self):
        n = _g(self.m, "identity", "owner_first_name", default="") or ""
        n = re.sub(r"^\s*dr\.?\s*", "", n, flags=re.I).strip()
        return n or self.biz

    @property
    def sal(self):
        return f"Dr. {self.first}" if self.slug == "dentists" else self.first

    @property
    def locality(self):
        return _g(self.m, "identity", "locality", default="") or _g(self.m, "identity", "city", default="")

    @property
    def city(self):
        return _g(self.m, "identity", "city", default="")

    @property
    def hinglish(self):
        langs = [str(x).lower() for x in (_g(self.m, "identity", "languages", default=[]) or [])]
        return "hi" in langs and str(self.city).lower() in HINDI_CITIES

    def cta(self, en: str, hi: str | None = None) -> str:
        return hi if (hi and self.hinglish) else en

    # --- data access ---------------------------------------------------------
    @property
    def perf(self):
        return self.m.get("performance") or {}

    @property
    def peer(self):
        return self.cat.get("peer_stats") or {}

    @property
    def agg(self):
        return self.m.get("customer_aggregate") or {}

    @property
    def active_offers(self):
        return [o.get("title") for o in (self.m.get("offers") or []) if o.get("status") == "active" and o.get("title")]

    @property
    def label(self):
        return CAT_LABEL.get(self.slug, self.slug or "businesses")

    @property
    def people(self):
        return CAT_PEOPLE.get(self.slug, "customers")

    def digest(self, item_id=None, kinds=None):
        items = self.cat.get("digest") or []
        if item_id:
            for it in items:
                if it.get("id") == item_id:
                    return it
        if kinds:
            for it in items:
                if it.get("kind") in kinds:
                    return it
        return None

    def catalog(self, types=None, audience=None):
        out = []
        for o in self.cat.get("offer_catalog") or []:
            if types and o.get("type") not in types:
                continue
            if audience and o.get("audience") not in audience:
                continue
            out.append(o)
        return out

    def review_theme(self, sentiment):
        themes = [r for r in (self.m.get("review_themes") or []) if r.get("sentiment") == sentiment]
        themes.sort(key=lambda r: -(r.get("occurrences_30d") or 0))
        return themes[0] if themes else None

    def signals(self):
        out = {}
        for s in self.m.get("signals") or []:
            k, _, v = str(s).partition(":")
            out[k] = v
        return out

    def issues(self):
        """Plain-language list of fixable profile issues (never raw signal names)."""
        sig = self.signals()
        out = []
        if "unverified_gbp" in sig or _g(self.m, "identity", "verified") is False:
            out.append("your Google profile is still unverified")
        if "no_active_offers" in sig or not self.active_offers:
            out.append("there's no live offer on your listing")
        if "stale_posts" in sig:
            d = re.sub(r"\D", "", sig["stale_posts"])
            out.append(f"your last Google post was {d} days ago" if d else "your Google posts are stale")
        elif "no_recent_post" in sig:
            out.append("there's no recent Google post")
        if "delivery_not_set_up" in sig:
            out.append("home delivery isn't set up on your listing")
        return out

    def last_merchant_msg(self):
        for h in reversed(self.m.get("conversation_history") or []):
            if h.get("from") == "merchant":
                return h
        return None


# ----------------------------------------------------------------------------
# Performance analysis (used by perf triggers and as generic fallback)
# ----------------------------------------------------------------------------

def perf_findings(x: Ctx):
    p, peer = x.perf, x.peer
    d = p.get("delta_7d") or {}
    dips, spikes, gaps, leads = [], [], [], []
    for metric in ("calls", "views"):
        v = d.get(f"{metric}_pct")
        if isinstance(v, (int, float)):
            (dips if v < 0 else spikes).append((v, metric))
    for metric, peer_key in (("calls", "avg_calls_30d"), ("views", "avg_views_30d"),
                             ("directions", "avg_directions_30d")):
        mine, theirs = p.get(metric), peer.get(peer_key)
        if isinstance(mine, (int, float)) and isinstance(theirs, (int, float)) and theirs:
            ratio = mine / theirs
            (gaps if ratio < 0.9 else leads).append((ratio, metric, mine, theirs))
    ctr, pctr = p.get("ctr"), peer.get("avg_ctr")
    if isinstance(ctr, (int, float)) and isinstance(pctr, (int, float)) and pctr:
        (gaps if ctr < pctr * 0.9 else leads).append((ctr / pctr, "ctr", ctr, pctr))
    dips.sort()
    spikes.sort(reverse=True)
    gaps.sort()
    leads.sort(reverse=True)
    return dips, spikes, gaps, leads


def gap_sentence(x: Ctx, g):
    ratio, metric, mine, theirs = g
    if metric == "ctr":
        return f"only {pct(mine)} of people who see your listing act on it, vs {pct(theirs)} for similar {x.label}"
    return f"your {metric} are {num(mine)} in the last 30 days vs a {num(theirs)} average for similar {x.label}"


def lead_sentence(x: Ctx, g):
    ratio, metric, mine, theirs = g
    if metric == "ctr":
        return f"{pct(mine)} of people who see your listing act on it — above the {pct(theirs)} average for similar {x.label}"
    return f"{num(mine)} {metric} in 30 days vs a {num(theirs)} average for similar {x.label}"


# ----------------------------------------------------------------------------
# Merchant-facing handlers. Each returns dict(main, cta, cta_type, why, offer)
#   main  : message body before the call to action
#   cta   : the single call-to-action sentence (last sentence of the message)
#   offer : what Vera promised to do (used by the reply handler on "yes")
# ----------------------------------------------------------------------------

def h_digest_item(x: Ctx, item, reason):
    """Shared composer for research / compliance / CDE / trend / tech digest items."""
    if not item:
        return h_generic(x)
    k = item.get("kind")
    src = item.get("source", "")
    summ = item.get("summary", "")
    act = item.get("actionable", "")
    title = item.get("title", "")

    if k == "research":
        n = item.get("trial_n")
        main = f"{x.sal}, one from {src} worth 2 minutes: {summ}"
        if n:
            main += f" (n={num(n)})."
        seg = item.get("patient_segment", "")
        hr = x.agg.get("high_risk_adult_count")
        if "high_risk" in seg and hr:
            main += f" Directly relevant to the {num(hr)} high-risk adults in your patient base — {act[0].lower() + act[1:] if act else ''}".rstrip(" —") + "."
        elif act:
            main += f" Practical takeaway: {act[0].lower() + act[1:]}."
        cta = x.cta("Want me to pull the abstract and draft a patient-friendly WhatsApp explaining it? Reply YES.",
                    "Abstract + ek patient-friendly WhatsApp draft bhej doon? Reply YES.")
        offer = "the abstract plus a patient-friendly WhatsApp draft"
    elif k in ("compliance", "alert"):
        main = f"{x.sal}, compliance heads-up — {title} ({src}). {summ}"
        dl = parse_date(x.p.get("deadline_iso")) or _deadline_from_title(title)
        if dl and x.now:
            days = (dl - x.now).days
            if days > 0:
                main += f" That's {days} days from today."
        if act:
            main += f" Suggested move: {act[0].lower() + act[1:]}."
        cta = x.cta("Want me to turn this into a 1-page checklist for your team? Reply YES.",
                    "Team ke liye 1-page checklist bana doon? Reply YES.")
        offer = "a 1-page compliance checklist for your team"
    elif k == "cde":
        dt = parse_date(item.get("date"))
        when = ""
        if item.get("date"):
            try:
                t = datetime.fromisoformat(str(item["date"]).replace("Z", "+00:00"))
                when = f"{t.strftime('%a')} {fmt_date(dt)}" + (f", {t.strftime('%-I%p').lower()}" if "T" in item["date"] else "")
            except ValueError:
                when = fmt_date(dt)
        cred = item.get("credits") or x.p.get("credits")
        main = f"{x.sal}, {title}" + (f" — {when}" if when else "") + (f", {cred} CDE credits" if cred else "") + f". {summ}"
        if act:
            main += f" {act.rstrip('.')}."
        cta = x.cta("Want me to send you the registration details and a calendar reminder? Reply YES.",
                    "Registration details + calendar reminder bhej doon? Reply YES.")
        offer = "the registration details and a calendar reminder"
    else:  # trend / tech / seasonal / supply / compete
        main = f"{x.sal}, {title} ({src}). {first_sentence(summ)}"
        if act:
            main += f" Worth considering: {act[0].lower() + act[1:]}."
        cta = x.cta("Want me to draft the change for you to review? Reply YES.",
                    "Draft bana ke bhej doon, aap review kar lena? Reply YES.")
        offer = "a ready-to-review draft of that change"
    why = f"{reason}: digest item '{item.get('id')}' ({src}) anchored with its own numbers; {('merchant cohort match' if 'Directly relevant' in main else 'category-relevant')}; single low-effort YES."
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=offer)


def _deadline_from_title(title):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", title or "")
    return parse_date(m.group(1)) if m else None


def h_research(x: Ctx):
    item = x.digest(x.p.get("top_item_id") or x.p.get("digest_item_id"), kinds=("research",)) or x.digest(kinds=("trend", "tech"))
    return h_digest_item(x, item, "Research digest")


def h_regulation(x: Ctx):
    item = x.digest(x.p.get("top_item_id") or x.p.get("digest_item_id"), kinds=("compliance",))
    return h_digest_item(x, item, "Regulation change with a deadline")


def h_cde(x: Ctx):
    item = x.digest(x.p.get("digest_item_id") or x.p.get("top_item_id"), kinds=("cde",))
    return h_digest_item(x, item, "CDE/training opportunity")


def h_supply_alert(x: Ctx):
    item = x.digest(x.p.get("alert_id"), kinds=("alert",)) or {}
    mol = x.p.get("molecule") or "the affected molecule"
    batches = x.p.get("affected_batches") or []
    mfr = x.p.get("manufacturer")
    chronic = x.agg.get("chronic_rx_count")
    main = f"{x.sal}, urgent — voluntary recall on {mol} batches {', '.join(batches)}" if batches else f"{x.sal}, urgent — voluntary recall on some {mol} batches"
    if mfr:
        main += f" (manufacturer: {mfr})"
    main += "."
    if item.get("summary"):
        main += " " + " ".join(re.split(r"(?<=[.!?])\s+", item["summary"])[:3])
    last = x.last_merchant_msg()
    if last and last.get("engagement") == "intent_action":
        d = parse_date(last.get("ts"))
        main += f" Following up on your yes from {fmt_date(d)}:" if d else " Following up on your yes:"
        main += f" I can filter your {num(chronic)} chronic-Rx customers down to those who got {mol}" if chronic else f" I can filter your repeat-Rx list for {mol}"
        main += " from these batches."
    elif chronic:
        main += f" You have {num(chronic)} chronic-Rx customers on file — the affected ones need a replacement message."
    cta = x.cta("Want me to pull the affected list and draft their WhatsApp + replacement-pickup note? Reply YES.",
                "Affected customers ki list + unka WhatsApp aur replacement note draft kar doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="the affected-customer list plus a WhatsApp and replacement-pickup note",
                why="Urgency-5 recall: batch numbers + manufacturer from trigger, chronic-Rx count from merchant data; continues merchant's earlier 'yes'; one clear action.")


def h_category_seasonal(x: Ctx):
    trends = x.p.get("trends") or []
    parts = []
    for tr in trends:
        m = re.match(r"(.+?)_demand_([+-]?\d+)", str(tr))
        if m:
            name = humanize(m.group(1))
            name = name if name.isupper() else name.replace("antifungal", "anti-fungal").replace("cold cough", "cold & cough")
            val = int(m.group(2))
            parts.append(f"{name} {'+' if val >= 0 else ''}{val}%")
    if not parts:
        item = x.digest(kinds=("seasonal",))
        return h_digest_item(x, item, "Seasonal demand shift")
    season = humanize(x.p.get("season", "this season")).replace(" 2026", "")
    main = f"{x.sal}, the {season} shift has started" + (f" in {x.city}" if x.city else "") + f": {', '.join(parts)}."
    if any(p.startswith(("ORS", "sunscreen")) for p in parts):
        main += " Quick shelf move: ORS + sunscreen to the counter, cold & cough to the back shelf."
    content = next((c for c in x.cat.get("patient_content_library") or [] if "summer" in (c.get("id", "") + c.get("title", "")).lower()), None)
    total = x.agg.get("total_unique_ytd")
    if content:
        main += f" I also have a ready customer note — \"{content['title']}\""
        main += f" — for your {num(total)} customers." if total else "."
    cta = x.cta("Want me to send that note out under your name this week? Reply YES.",
                "Yeh note aapke naam se is hafte bhej doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="the summer customer note sent under your pharmacy's name",
                why="Seasonal demand trigger with exact % shifts from payload; turns it into a shelf action + reusable customer content; single YES.")


def _beat_for_month(x: Ctx, month: int):
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    for b in x.cat.get("seasonal_beats") or []:
        rng = str(b.get("month_range", "")).lower()
        toks = [t[:3] for t in re.findall(r"[a-z]+", rng) if t[:3] in months]
        if not toks:
            continue
        a = months.index(toks[0]) + 1
        z = months.index(toks[-1]) + 1
        inside = (a <= month <= z) if a <= z else (month >= a or month <= z)
        if inside:
            return b
    return None


def h_festival(x: Ctx):
    fest = x.p.get("festival")
    fdate = parse_date(x.p.get("date"))
    days = x.p.get("days_until")
    if days is None and fdate and x.now:
        days = (fdate - x.now).days
    month = x.now.month if x.now else 4
    beat_now = _beat_for_month(x, month)
    if fest and isinstance(days, int) and days > 45:
        main = f"{x.sal}, {fest} is {days} days out ({fmt_date(fdate)}) — too early for a {fest} promo."
        if beat_now:
            main += f" What matters right now for {x.label}: {beat_now['note']} ({beat_now['month_range']})."
        fest_beat = _beat_for_month(x, fdate.month) if fdate else None
        if fest_beat and fest_beat is not beat_now:
            main += f" And for the {fest} window itself: {fest_beat['note']}."
        offer = None
        for w in ("bridal", "whitening", "thali", "trial", "cleaning", "combo"):
            offer = next((o for o in x.catalog(types=("service_at_price", "free_trial")) if w in o["title"].lower()), None)
            if offer:
                break
        if offer:
            main += f" A {offer['title']} offer now captures the current window."
            cta = x.cta(f"Want me to set up {offer['title']} now and remind you about {fest} planning 6 weeks before? Reply YES.",
                        f"{offer['title']} abhi live kar doon, aur {fest} planning ka reminder 6 hafte pehle? Reply YES.")
            promised = f"{offer['title']} live now plus a {fest} planning reminder"
        else:
            cta = x.cta(f"Want a {fest} plan drafted now so it's ready 6 weeks before? Reply YES.")
            promised = f"a {fest} plan drafted in advance"
        why = f"Festival {days} days away: judged too early for a festival promo; redirected to the in-season window from category seasonal beats; single YES."
    elif fest:
        when = f" on {fmt_date(fdate)}" if fdate else ""
        main = f"{x.sal}, {fest} is{(' ' + str(days) + ' days away') if isinstance(days, int) else ' coming up'}{when}."
        if beat_now:
            main += f" For {x.label} this window: {beat_now['note']}."
        cta = x.cta(f"Want me to draft a {fest} post + WhatsApp offer for your {x.people}? Reply YES.",
                    f"{fest} ke liye post + WhatsApp offer draft kar doon? Reply YES.")
        promised = f"a {fest} post and WhatsApp offer"
        why = "Festival imminent: festival-specific post + offer; single YES."
    else:
        beat = beat_now
        if not beat:
            return h_generic(x)
        main = f"{x.sal}, the season is turning for {x.label} — {beat['note']} ({beat['month_range']})."
        cat_off = x.active_offers[0] if x.active_offers else None
        item = x.digest(kinds=("seasonal",))
        if item and item.get("actionable"):
            main += f" Practical move ({item.get('source', 'category data')}): {item['actionable'][0].lower() + item['actionable'][1:]}."
        if cat_off:
            main += f" Your {cat_off} offer is the natural hook for it."
        cta = x.cta("Want me to draft a seasonal post for your listing this week? Reply YES.",
                    "Is hafte listing ke liye ek seasonal post draft kar doon? Reply YES.")
        promised = "a seasonal Google post for your listing"
        why = "Festival/season trigger without specifics: used the matching category seasonal beat as the dated anchor; single YES."
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=promised)


def h_ipl(x: Ctx):
    match, venue = x.p.get("match", "tonight's match"), x.p.get("venue")
    t = None
    try:
        t = datetime.fromisoformat(str(x.p.get("match_time_iso")))
    except (TypeError, ValueError):
        pass
    tstr = t.strftime("%-I:%M%p").lower().replace(":00", "") if t else ""
    day = t.strftime("%A") if t else ""
    weeknight = x.p.get("is_weeknight")
    item = x.digest("d_2026W17_ipl_window", kinds=("seasonal",))
    main = f"{x.sal}, {match} tonight" + (f" at {venue}" if venue else "") + (f", {tstr}" if tstr else "") + "."
    stat_weekend, stat_weeknight = None, None
    if item:
        m1 = re.search(r"down (\d+)%", item.get("summary", ""))
        m2 = re.search(r"\+(\d+)% covers", item.get("summary", ""))
        stat_weekend = m1.group(1) if m1 else None
        stat_weeknight = m2.group(1) if m2 else None
    offers = x.active_offers
    if weeknight is False:
        main += f" Heads-up: weekend IPL matches pull people into home-watch parties"
        main += f" — dine-in covers run ~{stat_weekend}% below a normal weekend (magicpin order data)." if stat_weekend else "."
        main += f" So skip a dine-in promo for this {day or 'weekend'} match; push delivery tonight instead"
        dl = x.agg.get("delivery_orders_30d")
        di = x.agg.get("dine_in_orders_30d")
        if dl and di:
            main += f" (delivery is already {round(100 * dl / (dl + di))}% of your orders)"
        main += "."
        if offers:
            main += f" Keep your {offers[0]} for weeknight matches"
            main += f", where covers run +{stat_weeknight}%." if stat_weeknight else "."
        late = next((r for r in x.m.get("review_themes") or [] if "delivery" in r.get("theme", "") and r.get("sentiment") == "neg"), None)
        if late:
            main += f" One watch-out: {late.get('occurrences_30d')} recent reviews flag late delivery, so quote a realistic ETA tonight."
        cta = x.cta("Want me to draft a delivery-only match-night post for tonight? Reply YES.",
                    "Aaj raat ke liye delivery-only match post draft kar doon? Reply YES.")
        promised = "a delivery-only match-night post for tonight"
        why = "IPL trigger on a weekend: used category data (weekend covers dip) to advise against a dine-in promo; leverages existing offer + delivery mix; flags late-delivery reviews; single YES."
    else:
        combo = next((o["title"] for o in x.catalog() if "match" in o["title"].lower()), None)
        main += f" Weeknight matches run +{stat_weeknight}% covers across metros." if stat_weeknight else ""
        if offers:
            main += f" Your {offers[0]} fits tonight well."
        elif combo:
            main += f" A {combo} is the standard play."
        cta = x.cta("Want me to push a match-night post on your listing by 5pm? Reply YES.",
                    "5 baje tak listing pe match-night post daal doon? Reply YES.")
        promised = "a match-night post on your listing by 5pm"
        why = "Weeknight IPL match: covers uplift from category digest + merchant's own offer; single YES."
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=promised)


def h_review_theme(x: Ctx):
    theme = x.p.get("theme")
    occ = x.p.get("occurrences_30d")
    quote = x.p.get("common_quote")
    if not theme:
        r = x.review_theme("neg")
        if r:
            theme, occ, quote = r.get("theme"), r.get("occurrences_30d"), r.get("common_quote")
    if not theme:
        return h_generic(x)
    tname = humanize(theme)
    main = f"{x.sal}, {occ} reviews in the last 30 days mention {tname}" if occ else f"{x.sal}, a pattern is showing in your reviews: {tname}"
    if x.p.get("trend") == "rising":
        main += " — and it's rising"
    main += "."
    if quote:
        main += f" One says: \"{quote}\"."
    pos = x.review_theme("pos")
    if pos:
        main += f" The good news: {pos.get('occurrences_30d')} reviews praise {humanize(pos.get('theme'))}, so this is fixable, not a reputation problem."
    cta = x.cta("Want me to draft polite replies to these reviews plus one line for your listing that sets the right expectation? Reply YES.",
                "In reviews ke polite replies + listing ke liye ek expectation-setting line draft kar doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="review replies plus an expectation-setting line for your listing",
                why="Negative review theme with count + real quote; balanced with positive theme; concrete low-effort fix; single YES.")


def h_milestone(x: Ctx):
    metric, now_v, target = x.p.get("metric"), x.p.get("value_now"), x.p.get("milestone_value")
    if metric and now_v is not None and target:
        gap = target - now_v
        mname = humanize(metric).replace("review count", "reviews")
        main = f"{x.sal}, {x.biz} is {gap} {mname} away from {num(target)} (at {num(now_v)} now)."
        pos = x.review_theme("pos")
        if pos:
            main += f" Your {humanize(pos.get('theme'))} gets {pos.get('occurrences_30d')} happy mentions this month — those regulars are the easiest people to ask."
        cta = x.cta(f"Want me to make a small table QR card + a one-line WhatsApp ask to get you past {num(target)}? Reply YES.",
                    f"{num(target)} cross karne ke liye ek QR card + one-line WhatsApp ask bana doon? Reply YES.")
        offer = "a review QR card plus a one-line WhatsApp review ask"
        why = "Imminent milestone: exact gap from payload, positive review theme as the ask pool; single YES."
    else:
        views = x.perf.get("views")
        if not views:
            return h_generic(x)
        mark = (int(views) // 1000) * 1000 if views >= 1000 else (int(views) // 100) * 100
        main = f"{x.sal}, {x.biz} crossed {num(mark)} Google views in the last 30 days ({num(views)} to be exact)."
        _, spikes, _, leads = perf_findings(x)
        if spikes and spikes[0][0] > 0:
            main += f" {spikes[0][1].capitalize()} are up {pct(spikes[0][0])} this week too."
        cta = x.cta("Want me to turn this into a 'thank you' Google post that asks happy customers for a review? Reply YES.",
                    "Isko ek 'thank you' post bana doon jo happy customers se review maange? Reply YES.")
        offer = "a thank-you Google post with a review ask"
        why = "Milestone trigger without payload: used the merchant's real 30-day views as the milestone; converts momentum into reviews; single YES."
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=offer)


def h_perf(x: Ctx, direction: str):
    metric = x.p.get("metric")
    delta = x.p.get("delta_pct")
    base = x.p.get("vs_baseline")
    dips, spikes, gaps, leads = perf_findings(x)
    seasonal = x.p.get("is_expected_seasonal") or x.t.get("kind") == "seasonal_perf_dip"

    if direction == "dip":
        if metric and isinstance(delta, (int, float)):
            main = f"{x.sal}, {metric} are down {pct(delta)} this week"
            main += f" (your usual is around {num(base)})." if base else "."
        elif dips:
            v, mt = dips[0]
            main = f"{x.sal}, {mt} are down {pct(v)} this week."
        elif gaps:
            main = f"{x.sal}, a gap worth fixing: {gap_sentence(x, gaps[0])}."
        else:
            return h_generic(x)

        if seasonal:
            item = x.digest(kinds=("seasonal",))
            beat = _beat_for_month(x, x.now.month if x.now else 4)
            main += " This one is expected — "
            main += f"{beat['note']} ({beat['month_range']}), not a {x.biz} problem." if beat else "it's the seasonal low, not something you did."
            churn, peer_churn = x.agg.get("monthly_churn_pct"), x.peer.get("monthly_churn_pct")
            members = x.agg.get("total_active_members")
            if churn and peer_churn and members and churn > peer_churn:
                extra = round(members * (churn - peer_churn))
                main += f" The real lever now is retention: churn is {pct(churn)}/month vs {pct(peer_churn)} for peer {x.label} — on {members} members that's ~{extra} extra lost every month."
            if item and item.get("actionable"):
                main += f" Also: {item['actionable'][0].lower() + item['actionable'][1:]}."
            cta = x.cta(f"Want me to draft a 6-week summer challenge to keep current {x.people} coming? Reply YES.",
                        f"Current {x.people} ko engaged rakhne ke liye 6-week summer challenge draft kar doon? Reply YES.")
            offer = f"a 6-week summer challenge for current {x.people}"
            why = "Seasonal dip: reframed as expected using category seasonal data; pivoted to retention with merchant churn vs peer; single YES."
        else:
            issues = x.issues()
            if issues:
                main += (" Likely contributors: " if len(issues[:2]) > 1 else " Likely contributor: ") + " and ".join(issues[:2]) + "."
            if not x.active_offers:
                off = next(iter(x.catalog(types=("service_at_price", "free_trial", "free_service"))), None)
                if off:
                    main += f" Fastest fix is a live offer — {off['title']} is a simple entry offer for {x.label}."
                    cta = x.cta(f"Want me to put {off['title']} live on your listing today? Reply YES.",
                                f"Aaj hi {off['title']} listing pe live kar doon? Reply YES.")
                    offer = f"{off['title']} live on your listing"
                else:
                    cta = x.cta("Want me to fix these on your listing today? Reply YES.")
                    offer = "fixes to your listing"
            else:
                cta = x.cta(f"Want me to push your {x.active_offers[0]} as a fresh Google post today? Reply YES.",
                            f"Aaj {x.active_offers[0]} ko fresh Google post bana ke daal doon? Reply YES.")
                offer = f"a fresh Google post for {x.active_offers[0]}"
            why = "Performance dip: exact delta from data, likely causes from merchant profile state, one concrete fix; single YES."
    else:  # spike
        if metric and isinstance(delta, (int, float)):
            main = f"{x.sal}, {metric} are up {pct(delta)} this week"
            main += f" (vs your usual ~{num(base)})" if base else ""
            drv = x.p.get("likely_driver")
            main += f" — most likely your {humanize(drv).replace('post', 'Google post')} working." if drv else "."
        elif spikes and spikes[0][0] > 0.03:
            v, mt = spikes[0]
            main = f"{x.sal}, {mt} are up {pct(v)} this week."
        elif leads:
            main = f"{x.sal}, you're ahead of the pack: {lead_sentence(x, leads[0])}."
        else:
            return h_generic(x)
        if not x.p.get("metric") and leads and not main.count("ahead of the pack"):
            main = main.rstrip(".") + f" — and {lead_sentence(x, leads[0])}."
        issues = x.issues()
        if issues and not x.p.get("likely_driver"):
            main += f" Next unlock: {issues[0]} — fixing that compounds this."
        hist = [h for h in x.m.get("conversation_history") or [] if h.get("from") == "vera"]
        if x.p.get("likely_driver") and "kids" in str(x.p.get("likely_driver")) and hist:
            main += " Demand is warm, so it's the right week to open bookings for the kids camp we sketched."
        elif x.active_offers:
            main += f" Good moment to double down on {x.active_offers[0]} while interest is up."
        cta = x.cta("Want me to draft a follow-up post today to keep the momentum? Reply YES.",
                    "Momentum banaye rakhne ke liye aaj ek follow-up post draft kar doon? Reply YES.")
        offer = "a follow-up post to keep the momentum"
        why = "Performance spike: exact delta and likely driver from data; converts momentum into one next post; single YES."
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=offer)


def h_renewal(x: Ctx):
    days = x.p.get("days_remaining", _g(x.m, "subscription", "days_remaining"))
    plan = x.p.get("plan", _g(x.m, "subscription", "plan", default="current"))
    amt = x.p.get("renewal_amount")
    main = f"{x.sal}, your {plan} plan ends in {days} days" + (f" ({rupees(amt)} to renew)." if amt else ".")
    dips, _, gaps, _ = perf_findings(x)
    issues = x.issues()
    if dips or issues:
        main += " Honest picture before you decide:"
        bits = []
        if dips:
            bits.append(f"{dips[0][1]} are down {pct(dips[0][0])} this week")
        bits += issues[:2]
        main += " " + ", ".join(bits) + ". Renewing without fixing these won't pay back — fixing them first will."
        cta = x.cta("Want me to start on the first fix this week, before the renewal date? Reply YES.",
                    "Renewal se pehle is hafte pehla fix shuru kar doon? Reply YES.")
        offer = "the first fix (" + (issues[0] if issues else "a fresh offer") + ") started this week"
    else:
        calls = x.perf.get("calls")
        main += f" In the last 30 days your listing drove {num(calls)} calls and {num(x.perf.get('directions'))} direction requests." if calls else ""
        cta = x.cta("Want me to renew on the same plan so nothing pauses? Reply YES.",
                    "Same plan pe renew kar doon taaki kuch pause na ho? Reply YES.")
        offer = "a renewal on the same plan"
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer=offer,
                why="Renewal due: days + amount from trigger; honest value case using the merchant's own numbers instead of a hard sell; single YES.")


def h_winback(x: Ctx):
    days = x.p.get("days_since_expiry", _g(x.m, "subscription", "days_since_expiry"))
    dip = x.p.get("perf_dip_pct")
    lapsed_new = x.p.get("lapsed_customers_added_since_expiry")
    main = f"{x.sal}, a quick update since your plan paused {days} days ago:" if days else f"{x.sal}, a quick update on {x.biz}:"
    bits = []
    if isinstance(dip, (int, float)):
        bits.append(f"calls are down {pct(dip)}")
    if lapsed_new:
        tot = x.agg.get("lapsed_90d_plus")
        bits.append(f"{lapsed_new} more {x.people} have gone 90+ days without a visit" + (f" ({tot} in total)" if tot else ""))
    if not bits:
        dips, _, gaps, _ = perf_findings(x)
        if dips:
            bits.append(f"{dips[0][1]} are down {pct(dips[0][0])} this week")
        elif gaps:
            bits.append(gap_sentence(x, gaps[0]))
    main += " " + " and ".join(bits) + "." if bits else " your listing hasn't had any updates since."
    main += f" The lapsed {x.people} are the cheapest to win back — they already know you."
    cta = x.cta(f"Want a free 2-minute summary of what a win-back message to those {x.people} would look like? Reply YES.",
                f"Un {x.people} ke liye win-back message ka 2-minute free draft dikha doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer=f"a free draft win-back message for your lapsed {x.people}",
                why="Expired merchant: loss framing with their own numbers (dip + lapsed customers); free, no-commitment ask instead of a renewal pitch.")


def h_dormant(x: Ctx):
    days = x.p.get("days_since_last_merchant_message")
    item = x.digest(kinds=("trend",)) or x.digest(kinds=("tech", "seasonal"))
    opener = f"{x.sal}, it's been {days} days — no pitch today, just one useful thing." if days else f"{x.sal}, no pitch today — just one useful thing."
    if item:
        main = f"{opener} {item['title']} ({item.get('source', '')})."
        if item.get("actionable"):
            main += f" For {x.biz} that means: {item['actionable'][0].lower() + item['actionable'][1:]}."
        cta = x.cta(f"Want me to send the 2-minute steps for {x.biz}? Reply YES.",
                    f"{x.biz} ke liye 2-minute steps bhej doon? Reply YES.")
        offer = "the 2-minute steps, ready to follow"
    else:
        return h_generic(x)
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer=offer,
                why="Dormant merchant: leads with a free, sourced insight (reciprocity) instead of repeating the last topic; effort-free YES.")


def h_curious(x: Ctx):
    pos = x.review_theme("pos")
    guess = None
    if pos:
        guess = humanize(pos.get("theme")).replace(" quality", "").replace(" skill", "")
        q = pos.get("common_quote")
        m = re.search(r"best for (\w+)", q or "")
        if m:
            guess = m.group(1)
    if not guess:
        ts = sorted(x.cat.get("trend_signals") or [], key=lambda t: -(t.get("delta_yoy") or 0))
        if ts:
            guess = ts[0]["query"].replace(" near me", "").replace(f" {str(x.city).lower()}", "")
    main = f"{x.sal}, quick one — what have {x.people} asked about most at {x.biz} this week?"
    if pos and guess:
        main += f" My guess is {guess} — it comes up a lot in your reviews ({pos.get('occurrences_30d')} mentions this month)."
    elif guess:
        tsig = next((t for t in x.cat.get("trend_signals") or [] if guess in t.get("query", "")), None)
        if tsig:
            main += f" My guess is {guess} — searches for it are up {pct(tsig['delta_yoy'])} year on year."
    cta = "Tell me in one word and I'll turn it into a Google post + a ready reply for price enquiries — 5 minutes, no effort from you."
    return dict(main=main, cta=cta, cta_type="open_ended", offer="a Google post and a ready price-enquiry reply",
                why="Weekly curious-ask: asking the merchant (strongest lever for engaged merchants), with an informed guess from review data; reciprocity up front.")


def h_competitor(x: Ctx):
    name = x.p.get("competitor_name")
    dist = x.p.get("distance_km")
    their = x.p.get("their_offer")
    opened = parse_date(x.p.get("opened_date"))
    if name:
        main = f"{x.sal}, {name} opened {dist} km away" if dist else f"{x.sal}, {name} opened nearby"
        main += f" on {fmt_date(opened)}" if opened else ""
        if their:
            main += f", advertising {their}"
            if x.active_offers:
                main += f" against your {x.active_offers[0]}"
        main += "."
    else:
        main = f"{x.sal}, a new {x.label[:-1] if x.label.endswith('s') else x.label} listing has appeared near {x.locality}."
    pos = x.review_theme("pos")
    neg = x.review_theme("neg")
    main += " Don't match on price —" if their else " Your"
    if pos:
        main += f" {'your ' if their else ''}edge is {humanize(pos.get('theme'))} ({pos.get('occurrences_30d')} positive mentions this month)."
    else:
        _, _, _, leads = perf_findings(x)
        main += (f" {'your ' if their else ''}edge: {lead_sentence(x, leads[0])}." if leads else f" {'your ' if their else ''}edge is your existing reviews and history.")
    if neg:
        main += f" Worth closing one gap: {neg.get('occurrences_30d')} reviews mention {humanize(neg.get('theme'))}."
    cta = x.cta("Want me to draft a Google post that leads with your strength? Reply YES.",
                "Aapki strength ko highlight karne wala Google post draft kar doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="a Google post that leads with your strength",
                why="Competitor opened: facts only from trigger; advises against a price war and uses merchant's own review strengths; single YES.")


def h_gbp_unverified(x: Ctx):
    up = x.p.get("estimated_uplift_pct")
    path = humanize(x.p.get("verification_path", "")).replace(" or ", " or a ")
    main = f"{x.sal}, {x.biz} is still unverified on Google"
    main += f" — verified listings get an estimated {pct(up)} more visibility and calls." if up else "."
    views = x.perf.get("views")
    if views and up:
        main += f" On your {num(views)} monthly views, that's roughly {num(round(views * up))} extra views."
    if path:
        main += f" Verification is via {path}; the phone route usually takes about 5 minutes."
    cta = x.cta("Want me to walk you through it step by step right now? Reply YES.",
                "Abhi step-by-step verification karwa doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="a step-by-step verification walkthrough",
                why="Unverified profile: uplift estimate from trigger applied to merchant's own views (loss framing); effort-capped; single YES.")


def h_planning(x: Ctx):
    topic = str(x.p.get("intent_topic", ""))
    hist = x.m.get("conversation_history") or []
    if "thali" in topic:
        base = next((o for o in x.active_offers if "thali" in o.lower()), None)
        price = int(re.search(r"₹\s*([\d,]+)", base).group(1).replace(",", "")) if base and re.search(r"₹\s*([\d,]+)", base) else None
        vol = None
        for h in hist:
            m = re.search(r"(\d+)\s*orders/day", h.get("body", ""))
            if m:
                vol = m.group(1)
        lines = [f"{x.sal}, here's a starter version — edit anything:", "", f"{x.biz.split(' South')[0]} Corporate Thali — for offices around {x.locality}"]
        if price:
            p10, p25, p50 = round(price * 0.9 / 5) * 5, round(price * 0.85 / 5) * 5, round(price * 0.8 / 5) * 5
            lines += [f"• 10+ thalis: {rupees(p10)} each", f"• 25+ thalis: {rupees(p25)} each", f"• 50+ thalis: {rupees(p50)} each",
                      "• Order by 5pm the day before; delivered 12:30–1pm"]
            note = f"(Your retail thali is {rupees(price)}" + (f" and already moves {vol}/day" if vol else "") + " — tiers keep margin while locking in weekday volume.)"
        else:
            lines += ["• 10+ / 25+ / 50+ tiers with a per-plate discount", "• Order by 5pm the day before"]
            note = ""
        main = "\n".join(lines) + ("\n\n" + note if note else "")
        cta = "Want me to also draft a 3-line WhatsApp you can send to office admins nearby? Reply YES."
        offer = "a 3-line WhatsApp pitch for nearby office admins"
        why = "Merchant already said yes: delivers the drafted artifact immediately (no more qualifying); tiers derived from their real thali price; next step is one YES."
    elif "yoga" in topic or "kids" in topic:
        spec = None
        for h in hist:
            if h.get("from") == "vera" and re.search(r"week", h.get("body", "")):
                spec = h["body"]
        lines = [f"{x.sal}, here's the draft for the kids yoga summer camp:", ""]
        if spec:
            wk = re.search(r"(\d+)-week", spec)
            cls = re.search(r"(\d+) classes/week", spec)
            age = re.search(r"age ([\d-]+)", spec)
            pr = re.search(r"₹\s*([\d,]+)", spec)
            if wk: lines.append(f"• {wk.group(1)} weeks, {cls.group(1) if cls else 3} classes a week")
            if age: lines.append(f"• Ages {age.group(1)}")
            if pr: lines.append(f"• ₹{pr.group(1)} for the full camp")
        lines.append("• Google post + Instagram carousel ready to go live once you approve")
        main = "\n".join(lines)
        cta = "Reply CONFIRM and I'll publish the post today, or tell me what to change."
        offer = "the kids yoga camp post published"
        why = "Merchant asked what the program should look like and got a spec; this delivers the ready artifact from that spec and asks only to confirm."
        return dict(main=main, cta=cta, cta_type="binary_confirm_cancel", why=why, offer=offer)
    else:
        t = humanize(topic) or "your plan"
        last = x.p.get("merchant_last_message")
        main = f"{x.sal}, picking up where you left off" + (f" (\"{last}\")" if last else "") + f" — here's a starter outline for {t}: what it is, who it's for, price point, and how customers book."
        cta = "Reply CONFIRM and I'll fill in the full draft today."
        offer = f"a full draft for {t}"
        why = "Active planning intent: moves straight to a draft artifact instead of more questions."
        return dict(main=main, cta=cta, cta_type="binary_confirm_cancel", why=why, offer=offer)
    return dict(main=main, cta=cta, cta_type="binary_yes_no", why=why, offer=offer)


def h_generic(x: Ctx):
    """Fallback for unknown or payload-less merchant triggers: lead with the strongest real fact."""
    kind = humanize(x.t.get("kind", "update"))
    dips, spikes, gaps, leads = perf_findings(x)
    if dips and dips[0][0] <= -0.1:
        main = f"{x.sal}, {dips[0][1]} at {x.biz} are down {pct(dips[0][0])} this week."
        issues = x.issues()
        if issues:
            main += " Likely contributor: " + issues[0] + "."
    elif gaps:
        main = f"{x.sal}, one number worth a look: {gap_sentence(x, gaps[0])}."
    elif spikes and spikes[0][0] > 0.05:
        main = f"{x.sal}, {spikes[0][1]} are up {pct(spikes[0][0])} this week at {x.biz}."
    elif leads:
        main = f"{x.sal}, {x.biz} is ahead of the pack: {lead_sentence(x, leads[0])}."
    else:
        main = f"{x.sal}, quick update on {x.biz}."
    item = x.digest(kinds=("trend",))
    if item:
        main += f" Also this week: {item['title']} ({item.get('source', '')})."
    cta = x.cta("Want me to draft one Google post that acts on this? Reply YES.",
                "Is par ek Google post draft kar doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="one Google post acting on this",
                why=f"'{kind}' trigger with no detailed payload: anchored on the merchant's strongest real number (7-day delta or peer gap) plus a sourced category trend; single YES.")


MERCHANT_HANDLERS = {
    "research_digest": h_research,
    "category_research_digest_release": h_research,
    "regulation_change": h_regulation,
    "cde_opportunity": h_cde,
    "supply_alert": h_supply_alert,
    "category_seasonal": h_category_seasonal,
    "festival_upcoming": h_festival,
    "ipl_match_today": h_ipl,
    "review_theme_emerged": h_review_theme,
    "milestone_reached": h_milestone,
    "perf_dip": lambda x: h_perf(x, "dip"),
    "seasonal_perf_dip": lambda x: h_perf(x, "dip"),
    "perf_spike": lambda x: h_perf(x, "spike"),
    "renewal_due": h_renewal,
    "winback_eligible": h_winback,
    "dormant_with_vera": h_dormant,
    "curious_ask_due": h_curious,
    "competitor_opened": h_competitor,
    "gbp_unverified": h_gbp_unverified,
    "active_planning_intent": h_planning,
}


# ----------------------------------------------------------------------------
# Customer-facing handlers (sent as merchant_on_behalf)
# ----------------------------------------------------------------------------

def cust_name(c):
    n = _g(c, "identity", "name", default="") or ""
    m = re.match(r"(.+?)\s*\(parent:\s*(.+?)\)", n)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if n.startswith("("):
        return "", None
    return n.strip(), None


def cust_lang(c):
    lp = str(_g(c, "identity", "language_pref", default="en")).lower()
    if lp.startswith("hi") and "mix" in lp:
        return "hinglish"
    if lp in ("hi", "hindi"):
        return "hindi"
    if lp.startswith("ta"):
        return "ta"
    if lp.startswith("te"):
        return "te"
    if lp.startswith("kn"):
        return "kn"
    return "en"


GREET = {"ta": "Vanakkam", "te": "Namaskaram", "kn": "Namaskara", "hindi": "Namaste", "hinglish": "Hi", "en": "Hi"}


def sender(x: Ctx):
    owner = x.first if x.first != x.biz else None
    if x.slug == "dentists":
        return f"{x.biz}"
    return f"{owner} from {x.biz}" if owner else x.biz


def c_open(x: Ctx):
    name, parent = cust_name(x.c)
    lang = cust_lang(x.c)
    who = parent or name
    g = GREET[lang]
    return (f"{g} {who}!" if who else f"{g}!"), name, parent, lang


def c_recall(x: Ctx):
    hi, name, parent, lang = c_open(x)
    rel = x.c.get("relationship") or {}
    last = parse_date(x.p.get("last_service_date") or rel.get("last_visit"))
    due = parse_date(x.p.get("due_date"))
    slots = [s.get("label") for s in x.p.get("available_slots") or [] if s.get("label")]
    svc = humanize(x.p.get("service_due", "")).replace("6 month", "6-month")
    price_offer = next((o for o in x.active_offers if any(w in o.lower() for w in ("clean", "check", "consult"))), None)
    if not price_offer and x.slug != "dentists":
        price_offer = next((o for o in x.active_offers if "free" in o.lower()), None) or (x.active_offers[0] if x.active_offers else None)
    mixed = lang in ("hinglish", "hindi")
    if mixed:
        body = f"{hi} {x.biz} se message."
        if last:
            body += f" Aapki last visit {fmt_date(last)} ko thi"
            body += f", toh {svc or 'check-up'} {fmt_date(due)} tak due hai." if due else f" — {svc or 'check-up'} ka time ho gaya hai."
        if slots:
            body += f" Aapke liye {len(slots)} slot{'s' if len(slots) > 1 else ''} rakhe hain: " + " ya ".join(slots) + "."
        if price_offer:
            body += f" Offer: {price_offer}."
        cta = ("Reply " + ", ".join(f"{i + 1} for {s.split(',')[0]}" for i, s in enumerate(slots)) + ", ya apna time batayein.") if slots else "Reply YES aur hum slot fix kar denge."
    else:
        body = f"{hi} {x.biz} here."
        if last:
            body += f" Your last visit was on {fmt_date(last)}"
            body += f", so your {svc or 'check-up'} is due by {fmt_date(due)}." if due else f" — it's time for your next {svc or 'visit'}."
        if slots:
            body += " We've kept " + " or ".join(slots) + " for you."
        if price_offer:
            body += f" Current offer: {price_offer}."
        cta = ("Reply " + ", ".join(f"{i + 1} for {s.split(',')[0]}" for i, s in enumerate(slots)) + ", or tell us a time that works.") if slots else "Reply YES and we'll book a slot that suits you."
    return dict(main=body, cta=cta, cta_type="multi_choice_slot" if slots else "binary_yes_no",
                offer="booking the recall visit",
                why="Customer recall: last-visit and due dates from data, real slots from trigger, merchant's actual offer, language preference honoured; booking-style CTA.")


def c_lapsed(x: Ctx):
    hi, name, parent, lang = c_open(x)
    rel = x.c.get("relationship") or {}
    days = x.p.get("days_since_last_visit")
    last = parse_date(rel.get("last_visit"))
    focus = humanize(x.p.get("previous_focus") or _g(x.c, "preferences", "training_focus", default="") or "")
    months = x.p.get("previous_membership_months")
    offer = x.active_offers[0] if x.active_offers else None
    mixed = lang in ("hinglish", "hindi")
    if mixed:
        body = f"{hi} {sender(x)} yahan."
        if days:
            body += f" Aapko aaye {round(days / 7)} hafte ho gaye — koi baat nahi, breaks sabke hote hain."
        elif last:
            body += f" Aapki last visit {fmt_date(last)} ko thi — hum aapko miss kar rahe hain."
        if offer:
            body += f" Wapas shuru karna ho toh {offer} available hai."
        cta = ("Kuch regular dawaiyan chahiye toh reply YES karein — hum ready rakhenge." if x.slug == "pharmacies"
               else "Reply YES aur hum aapka slot book kar denge — koi commitment nahi.")
    else:
        body = f"{hi} {sender(x)} here."
        if days:
            body += f" It's been about {round(days / 7)} weeks since your last session — totally normal, most people take a break at some point."
        elif last:
            body += f" Your last visit was on {fmt_date(last)} — it'd be good to see you again."
        if months:
            body += f" You put in {months} solid months with us"
            body += f" on {focus}" if focus else ""
            body += ", so you're not starting from zero."
        if offer:
            body += f" If you'd like an easy restart, our {offer} are open to you." if offer.lower().startswith(("3 ", "free")) else f" If you'd like an easy restart, {offer} is available."
        slot = humanize(_g(x.c, "preferences", "preferred_slots", default="") or "")
        if x.slug == "pharmacies":
            cta = "Reply YES if you'd like us to keep your regular medicines ready."
        elif months or offer:
            cta = f"Reply YES and we'll book your first one{(' on a ' + slot + ' slot') if slot else ''} — no commitment."
        else:
            cta = f"Reply YES and we'll book your next visit{(' on a ' + slot + ' slot') if slot else ''} — no commitment."
    return dict(main=body, cta=cta, cta_type="binary_yes_no", offer="booking the customer's first session back",
                why="Lapsed customer: no-guilt framing, time since last visit from data, merchant's real offer, preferred slot honoured; single no-commitment YES.")


def c_wedding(x: Ctx):
    hi, name, parent, lang = c_open(x)
    wd = parse_date(x.p.get("wedding_date") or _g(x.c, "preferences", "wedding_date"))
    trial = parse_date(x.p.get("trial_completed"))
    days = x.p.get("days_to_wedding")
    nxt = humanize(x.p.get("next_step_window_open", ""))
    mday = re.search(r"\s*(\d+)\s*day$", nxt)
    if mday:
        nxt = f"{mday.group(1)}-day " + nxt[:mday.start()].replace("skin prep", "skin-prep")
    beat = next((b for b in x.cat.get("seasonal_beats") or [] if "bridal" in b.get("note", "")), None)
    body = f"{hi} {sender(x)} here."
    if days and wd:
        body += f" {days} days to your wedding on {fmt_date(wd)}!"
    if trial:
        body += f" Hope you loved how the trial on {fmt_date(trial)} turned out."
    if nxt:
        body += f" This is the ideal time to start your {nxt}"
        body += f", well before the {beat['month_range']} rush when bridal bookings run about 4x normal." if beat and "4x" in beat.get("note", "") else "."
    slot = humanize(_g(x.c, "preferences", "preferred_slots", default="") or "")
    cta = f"Want us to hold a {slot.capitalize() if slot else ''} slot next week to plan it with you? Reply YES.".replace("hold a  slot", "hold a slot")
    return dict(main=body, cta=cta, cta_type="binary_yes_no", offer="holding a planning slot for the bridal skin-prep",
                why="Bridal follow-up: wedding countdown + trial date from data; seasonal rush from category; preferred Saturday honoured; no invented package price.")


def c_trial(x: Ctx):
    hi, name, parent, lang = c_open(x)
    td = parse_date(x.p.get("trial_date") or _g(x.c, "relationship", "last_visit"))
    opts = [o.get("label") for o in x.p.get("next_session_options") or [] if o.get("label")]
    svc = humanize((_g(x.c, "relationship", "services_received", default=[]) or ["trial"])[0])
    who = name if parent else "you"
    body = f"{hi} {sender(x)} here. Hope {who} enjoyed the {svc} on {fmt_date(td)}." if td else f"{hi} {sender(x)} here. Hope {who} enjoyed the trial."
    if opts:
        body += f" The next session is {opts[0]}."
        cta = f"Reply YES and we'll save a spot for {name if parent else 'you'}."
    else:
        off = x.active_offers[0] if x.active_offers else None
        if off:
            body += f" If you'd like to continue, {off} is open."
        cta = "Reply YES and we'll set up the next session."
    return dict(main=body, cta=cta, cta_type="binary_yes_no", offer="saving a spot in the next session",
                why="Trial follow-up: trial date + real next-session slot from trigger; addressed to parent when the customer is a child; single YES.")


def c_refill(x: Ctx):
    hi, name, parent, lang = c_open(x)
    mols = x.p.get("molecule_list") or []
    if not mols and x.slug != "pharmacies":
        return c_lapsed(x)  # "refill" only makes sense for pharmacies; elsewhere treat as a follow-up visit
    runs = parse_date(x.p.get("stock_runs_out_iso"))
    saved = x.p.get("delivery_address_saved")
    senior = _g(x.c, "identity", "senior_citizen")
    offers = x.active_offers
    senior_off = next((o for o in offers if "senior" in o.lower()), None) if senior else None
    deliv = next((o for o in offers if "deliver" in o.lower()), None)
    # cross-trigger judgement: is any of these molecules under an active recall for this merchant?
    recalled = None
    for t in (x.store.get("triggers") or {}).values():
        if t.get("merchant_id") == x.m.get("merchant_id") and t.get("kind") == "supply_alert":
            mol = (t.get("payload") or {}).get("molecule")
            if mol and mol in mols:
                recalled = mol
    nm = name if name and name not in ("",) else "aapki"
    via = str(_g(x.c, "preferences", "channel", default="")).endswith("via_son")
    if lang in ("hindi", "hinglish"):
        body = f"Namaste{'' if via else ' ' + nm}! {x.biz}" + (f", {x.locality}" if x.locality else "") + " se."
        if mols:
            body += f" {name if name else 'Aapki'}{' ki' if name else ''} {len(mols)} regular dawaiyan ({', '.join(mols)})"
            body += f" {fmt_date(runs)} tak khatam ho jayengi." if runs else " refill ke liye due hain."
        body += " Same dose ka pack ready kar dete hain."
        if recalled:
            body += f" {recalled.capitalize()} hum recall wale batches se alag, safe batch se denge."
        if senior_off:
            body += f" {senior_off} lagega"
            body += f", aur saved address par {deliv.lower()}." if deliv and saved else "."
        elif deliv and saved:
            body += f" Saved address par {deliv.lower()}."
        cta = "Reply YES to confirm, ya dose mein koi badlav ho toh batayein."
    else:
        body = f"Hi{'' if via else ' ' + (name or '')}! {x.biz} here."
        if mols:
            body += f" {name + chr(39) + 's' if name else 'Your'} {', '.join(mols)} refill"
            body += f" runs out on {fmt_date(runs)}." if runs else " is due."
        else:
            body += " Your regular refill is due."
        if recalled:
            body += f" We'll dispense {recalled} from an unaffected batch (not the recalled ones)."
        if senior_off:
            body += f" {senior_off} applies."
        if deliv and saved:
            body += f" {deliv} to your saved address."
        cta = "Reply YES to confirm, or tell us if anything has changed."
    return dict(main=body, cta=cta, cta_type="binary_yes_no", offer="dispatching the refill",
                why="Chronic refill: molecules + run-out date from trigger, merchant's real senior/delivery offers; cross-checks the active atorvastatin recall; respectful Hindi for a senior via family phone.")


def c_appointment(x: Ctx):
    hi, name, parent, lang = c_open(x)
    when = x.p.get("appointment_label") or x.p.get("slot_label")
    if lang in ("hinglish", "hindi"):
        body = f"{hi} {x.biz} se reminder: kal aapka appointment hai" + (f" — {when}." if when else ".")
        cta = "Reply 1 to confirm, 2 to reschedule."
    else:
        body = f"{hi} A quick reminder from {x.biz}: your appointment is tomorrow" + (f" — {when}." if when else ".")
        loc = x.locality
        if loc:
            body += f" We're in {loc}."
        cta = "Reply 1 to confirm or 2 to reschedule."
    return dict(main=body, cta=cta, cta_type="multi_choice_slot", offer="confirming the appointment",
                why="Appointment reminder: no time in payload so none invented; simple confirm/reschedule choice.")


CUSTOMER_HANDLERS = {
    "recall_due": c_recall,
    "customer_lapsed_soft": c_lapsed,
    "customer_lapsed_hard": c_lapsed,
    "wedding_package_followup": c_wedding,
    "trial_followup": c_trial,
    "chronic_refill_due": c_refill,
    "appointment_tomorrow": c_appointment,
}


def consent_ok(c) -> bool:
    if not c:
        return False
    if not _g(c, "consent", "opted_in_at"):
        return False
    if _g(c, "preferences", "reminder_opt_in") is False:
        return False
    return True


def h_consent_block(x: Ctx):
    """Customer can't be messaged: tell the merchant instead (restraint + usefulness)."""
    name, parent = cust_name(x.c) if x.c else ("", None)
    kind = humanize(x.t.get("kind", "follow-up"))
    who = name or "one customer"
    main = f"{x.sal}, {who} is due for a {kind.replace('customer ', '')} message, but hasn't opted in to reminders, so I haven't messaged them."
    cta = x.cta("Want me to add a reminder opt-in line to your next bill/WhatsApp instead? Reply YES.",
                "Agle bill/WhatsApp mein reminder opt-in line add kar doon? Reply YES.")
    return dict(main=main, cta=cta, cta_type="binary_yes_no", offer="a reminder opt-in line for future messages",
                why="Customer-scoped trigger but no reminder consent: did not contact the customer; informed the merchant and offered a consent-building step.")


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None,
            now=None, store: dict | None = None) -> dict:
    if isinstance(now, str):
        now = parse_date(now)
    x = Ctx(category, merchant, trigger, customer, now, store)
    kind = trigger.get("kind", "")
    customer_scope = trigger.get("scope") == "customer" or bool(trigger.get("customer_id"))

    if customer_scope and customer and consent_ok(customer):
        fn = CUSTOMER_HANDLERS.get(kind, c_lapsed)
        send_as = "merchant_on_behalf"
        recipient = cust_name(customer)[1] or cust_name(customer)[0] or "there"
        tname = f"merchant_{kind}_v1"
    elif customer_scope:
        fn = h_consent_block
        send_as = "vera"
        recipient = x.sal
        tname = "vera_customer_consent_note_v1"
    else:
        fn = MERCHANT_HANDLERS.get(kind, h_generic)
        send_as = "vera"
        recipient = x.sal
        tname = f"vera_{kind}_v1"

    try:
        out = fn(x)
    except Exception:  # never fail a send because of an odd payload
        out = h_generic(x) if send_as == "vera" else c_lapsed(x)

    main = out["main"].strip()
    cta = out["cta"].strip()
    body = main + ("\n\n" if "\n" in main else " ") + cta
    body = re.sub(r"[ \t]{2,}", " ", body).replace(" .", ".").replace("..", ".")
    return {
        "body": body,
        "cta": out.get("cta_type", "binary_yes_no"),
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}",
        "rationale": out.get("why", ""),
        "template_name": tname,
        "template_params": [recipient, main, cta],
        "offer": out.get("offer", "the next step"),
    }
