You look for the LARGEST company in the world (outside Russia/CIS) that runs the SAME business model as described:
same customer type, same core product, same way of charging. It can be the described company itself.
The analog must make its money MAINLY from this kind of product. A big-tech company or an AI lab that merely
has a similar feature is NOT an analog (a group-chat app's analog is not xAI or Meta; an AI grading tool's analog
is not OpenAI). If you only find such companies, return analog_name = null.

Use web search. For the analog you choose, collect EVERY valuation figure you find, from different sources:
public market cap, last funding round valuation, or a press estimate. Do NOT average or pick — list them all.

Rules:
- Every value must have the URL where you saw it, and the date of that source (ISO, "2025" is ok).
- value_usd: a plain number in US dollars (e.g. 1200000000).
- If no comparable company is found, return analog_name = null and an empty values list.
- same_model_reason: one sentence in Russian on why the analog's model is the same.
