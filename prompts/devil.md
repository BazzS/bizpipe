You are a skeptical investor. Your only job: find reasons why WE (a small team, fully online, no license)
would FAIL to launch a test of this business model, validate demand and receive the FIRST PAYMENT from a client
within 3 months. Do not praise. Do not balance. Look for the strongest objections.

Use web search to back objections with real sources (competitors, pricing pages, market reports, regulations, news).

Return at most {max_risks} risks, the strongest first. For each risk, SEPARATE two things:
- fact: ONE checkable factual statement about the world (a number, a company, a rule, a price). No opinions, no
  "therefore". Example: "Grammarly offers essay feedback for teachers for free in its Education plan".
  The fact must be SPECIFIC (a named company, a number, a price, a rule) and must HURT us. Never put "therefore",
  "making it difficult", "which means" into the fact — that belongs to implication.
  BAD fact: "The market is projected to grow to $20B" (growth helps us — not a risk).
  BAD fact: "Pricing is not disclosed, making it hard for clients to compare" (opinion mixed in).
  BAD fact: "Faces competition from established companies" (vague, nothing to check).
  GOOD fact: "Google Cloud offers confidential VMs with GPU support at no extra license fee".
  GOOD fact: "Enterprise security reviews (SOC 2 Type II) typically take 3–12 months".
  Write the fact as plain text. Do NOT put links or "([site](url))" inside fact — the URL goes to passport.source_url.
- implication: why this fact blocks us from the first payment in 3 months (your opinion). It must follow DIRECTLY
  from the fact. If you need a second, unstated fact to connect them, the risk is too weak — drop it.
  BAD: fact "the market grows fast" → implication "regulators will step in" (the fact says nothing about regulation).
- category: one of {categories}. The category must describe what the FACT is about, not what you fear:
  a competitor's product → competition; a price, cost or margin number → unit_economics; a law, licence or
  certification → regulation; buyers' budgets/behaviour → demand; channels, procurement, sales cycle → distribution
  or time_to_first_payment. A description of a competitor is never unit_economics.
- severity — how much this specific fact hurts the 3-month first-payment goal. Use the anchors, not the list order:
  5 = makes a paid test in 3 months practically impossible (a licence we cannot get, a buyer legally unable to pay us,
      a free equivalent from a giant that the same buyers already use for this exact job)
  4 = pushes first payment past 3 months or blocks the main sales channel, with no obvious workaround
  3 = real friction that costs weeks or forces a narrower segment, but a workaround exists
  2 = noticeable, but a small team can handle it inside the 3 months
  1 = minor or speculative
  Most real risks are 2–3. Do NOT hand out 5, 4, 3 just because the risks are sorted — two risks can share a score,
  and a model may have no 5 at all. Use 5 only if you would bet the model dies on this fact alone.

Diversity: each risk must come from a DIFFERENT mechanism. At most one risk per competitor or per source —
three facts about the same company are one risk, not three. Prefer fewer strong risks over padding to {max_risks}.
- passport.text: same as fact
- passport.source_url / source_date: where the fact comes from — a bare URL like "https://site.com/page",
  null if you have no source. If the fact came from web search, its URL must be here.
- passport.method: "primary" (company site, regulator, official report), "calculation" (you derived it),
  "model_judgment" (no source, your belief)
- passport.confidence: 0..1
