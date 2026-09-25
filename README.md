# Vera+ — magicpin AI Challenge submission

## Approach: decide in code, write from context, never invent

Every message is built in two steps. First, code decides what to say: one handler per trigger kind (20+ kinds) picks the single fact that matters. That fact might be a digest item's numbers, a 7-day delta, a peer gap, a review theme, or a slot. Second, the handler writes the message from templates that only use values present in the four contexts, or simple arithmetic on them (for example, "~5 extra members lost monthly" = 245 × (10% − 8%)).

**Why no LLM:** the three failures the judge punishes hardest are fabrication, non-determinism and timeouts. A template composer can't hallucinate, gives the same output for the same input, and answers in about 1ms (20 actions per tick easily fit the budget).

**Judgment, not just templating:**

- *IPL on a weekend:* the bot advises against a dine-in promo, using the category's "weekend covers −12%" data. It pushes delivery instead and flags the merchant's late-delivery reviews.
- *Diwali 188 days out:* the bot says it's too early and redirects to the current bridal window.
- *Chronic refill:* the bot notices the atorvastatin recall trigger for the same pharmacy and promises an unaffected batch.
- *Renewal:* the bot makes an honest value case (fix the dip first) rather than a hard sell.
- *Customer without reminder consent:* the bot does not message the customer. It tells the merchant instead and offers an opt-in line.

**Voice and language:** each category keeps its own register (clinical-peer for dentists, operator-to-operator for restaurants, and so on), and "Dr." is used for dentists. Hindi-English code-mix is used for Hindi-belt merchants and for customers with `hi` / `hi-en mix` preferences. Regional greetings (Vanakkam / Namaskara / Namaskaram) are used for ta, kn and te customers. Every message has one CTA, placed last, and no URLs.

**Triggers with no payload:** half the canonical pairs are generated triggers with no payload. These fall back to the merchant's strongest real number: a 7-day delta, a gap versus `peer_stats`, or a lead over peers. They also use the category's seasonal beat or digest item, so specificity never comes from thin air.

**Multi-turn (`replies.py`):** incoming replies are routed by rules in a fixed priority order:

1. **Auto-reply** (canned-phrase patterns plus verbatim repeats, counted per merchant across conversations): first time, one flag message; second time, wait 24h; third time, end.
2. **Hostile / opt-out:** end immediately and suppress all further proactive sends to that merchant.
3. **Commitment** ("ok let's do it", "haan kar do"): switch straight to action, then a single CONFIRM, then close.
4. **Wants time** ("later", "busy"): wait 4h.
5. **Off-topic** (GST etc.): decline in one line, then steer back to the thread.
6. **Question:** a short honest answer, then restate the one pending step.
7. **Everything else:** acknowledge and advance.

Anti-repetition applies per conversation and per merchant.

**Operational:** zero dependencies (standard library HTTP server). The context store is idempotent: a stale or equal version returns 409 and a higher version replaces the old one atomically. Suppression-key dedup, at most 2 merchant-facing sends per merchant per tick, and customer sends wait until that customer's context has arrived. `/v1/teardown` wipes state. No handler can return a 500: any error yields a safe default.

## Tradeoffs

- Templates are less varied than LLM prose. I traded some fluency for guaranteed grounding and determinism.
- Free-form merchant questions get safe, generic answers rather than rich ones.
- `expires_at` is not used to hard-filter triggers, because simulated and wall-clock time can disagree. Suppression keys prevent re-sends instead.

## What would have helped most

Real merchant appointment slots and offer prices for generated merchants; a merchant-level "preferred language" field (rather than inferring it from city + `languages`); and the post-submission digest/trigger kinds, to add dedicated handlers.

## Run

```bash
python bot.py                      # serves on $PORT (default 8080)
python dataset/generate_dataset.py --seed-dir dataset --out expanded && python make_submission.py
```
