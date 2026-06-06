# csa-wrangler

A serverless, event-driven meal planner for CSA (Community Supported Agriculture) shares.

Forward your weekly CSA email and a few seconds later you get back a formatted dinner
plan — protein-driven mains paired with veggie sides that use up the week's share, plus a
categorized grocery list. Everything runs on AWS and is defined as infrastructure-as-code.

```
CSA email  ──►  Gmail filter forwards  ──►  plan@csa.<domain>
                                                  │  (MX → SES inbound)
                                                  ▼
                              SES receipt rule:  store raw MIME in S3
                                                  │  + invoke Lambda
                                                  ▼
   recipes_tagged.json (S3) ──►   planner Lambda   ◄── plan history (DynamoDB)
                                                  │
                          parse veggies → build plan → render HTML
                                                  │
                   SES send to your inbox  +  archive HTML  +  log to history
```

## What it does

1. **Receive** — your CSA emails arrive at a Workspace address; a Gmail filter forwards them
   to `plan@csa.<your-domain>`. A dedicated subdomain (e.g. `csa.wendyk.org`) is verified in
   SES with its own MX, so your real mail keeps flowing through Workspace untouched.
2. **Parse** — the Lambda reads the raw MIME from S3, extracts the `Share contents:` line,
   and normalizes each item to a canonical vegetable vocabulary (e.g. `lacinato kale` → `kale`,
   `baby bok choi` → `bok choy`), dropping salad/lettuce/microgreen filler.
3. **Plan** — it builds an `N`-night dinner plan from your recipe corpus:
   - greedily covers as many of the week's veggies as possible with **main** dishes,
   - keeps protein variety (each meat protein appears at most twice),
   - avoids any recipe used in the last `NO_REPEAT_WEEKS` plans (unless it's the *only* way
     to cover a veggie),
   - fills any remaining nights with variety, then
   - pairs a **side** dish with each feature veggie (your "roast chicken + glazed carrots"),
     so even veggies with no main recipe (beets, kohlrabi) still get used.
4. **Send** — it renders the HTML email (per-night cards + a grocery list with this week's
   CSA veggies subtracted), emails it to you, archives a copy to S3, and logs the plan so
   next week knows what not to repeat.

Unparseable or non-CSA emails get a short diagnostic reply instead of failing silently; real
errors go to a dead-letter queue and raise a CloudWatch alarm.

## Repository layout

```
src/planner/        the Lambda package (CodeUri)
  app.py            handler: SES event → parse → plan → render → send → archive → log
  parse.py          CSA email → canonical week veggies
  planner.py        the planning algorithm (cover, protein cap, no-repeat, side-pairing)
  render.py         HTML email + grocery categorizer
  stores.py         S3 corpus/archive, DynamoDB history + idempotency, SES send
  config.py         config from SSM Parameter Store / env
  recipe_tagging.py canonical veggie + protein vocabulary (shared with corpus tagging)
template.yaml       AWS SAM stack (all infrastructure)
scripts/            local tools (corpus building/tagging — not part of the Lambda)
  add_recipes.py    scrape + tag recipe URLs and merge into the corpus
  recipe_tagging.py source of truth for the tagging vocabulary
tests/              pytest suite (parser, planner, renderer)
```

> The recipe corpus (`recipes_tagged.json`) and any scraped data are **not** committed — they
> live in S3 and are gitignored. See [Recipe corpus](#recipe-corpus) below.

## Prerequisites

- An AWS account, in a region where **SES inbound** is supported (`us-west-2` is the default here).
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
  and the AWS CLI, authenticated (`aws sso login` or credentials in `~/.aws`).
- **Python 3.13** on your PATH (matches the Lambda runtime) for `sam build`.
  `brew install python@3.13`, or build with `sam build --use-container` (Docker).
- A domain whose DNS you control (this guide uses **Cloudflare**, DNS-only/unproxied records).

## Deploy

```bash
# first time — interactive, saves a samconfig.toml
sam build
sam deploy --guided \
  --parameter-overrides Domain=csa.example.com PlanAddress=plan@csa.example.com \
                        FromEmail=plan@csa.example.com RecipientEmail=you@example.com

# subsequent deploys
sam build && sam deploy
```

The stack creates: the planner Lambda + IAM role, one S3 bucket (prefixes `raw-emails/`,
`corpus/`, `plan-archive/`), a DynamoDB table (plan history + idempotency, with TTL), the SES
domain identity + DKIM, the SES receipt rule set + rule, SSM config parameters, an (empty)
Secrets Manager placeholder, and an SQS DLQ + CloudWatch alarms wired to an SNS topic.

### Post-deploy steps (one-time)

CloudFormation can't do these, so run them after the first deploy (the stack **Outputs** print
the exact commands):

```bash
# 1. Activate the receipt rule set (SES allows only one active set per account/region)
aws ses set-active-receipt-rule-set --rule-set-name csa-wrangler-ruleset --region us-west-2

# 2. Upload the recipe corpus
aws s3 cp recipes_tagged.json s3://<bucket-from-outputs>/corpus/recipes_tagged.json

# 3. Verify the recipient address (SES sandbox only sends to verified addresses)
aws ses verify-email-identity --email-address you@example.com --region us-west-2
#    → click the link in the verification email

# 4. Confirm the SNS subscription email so you get failure alerts
```

> Sandbox is fine because you only ever email yourself. `ses:SendEmail` is scoped to the
> sending domain + the recipient identity; if you later request production access you can drop
> the recipient grant.

### DNS records (Cloudflare, all DNS-only / grey cloud)

The DKIM/TXT values come from the stack **Outputs** after deploy. For `csa.example.com`:

| Type | Name | Value |
|---|---|---|
| MX | `csa` | `inbound-smtp.us-west-2.amazonaws.com` (priority 10) |
| TXT | `_amazonses.csa` | the `_amazonses` verification token (output) |
| CNAME ×3 | `<token>._domainkey.csa` | `<token>.dkim.amazonses.com` (DKIM, output) |
| TXT *(optional)* | `csa` | `v=spf1 include:amazonses.com ~all` |
| TXT *(optional)* | `_dmarc.csa` | `v=DMARC1; p=none;` |

Do **not** proxy these (MX and mail-auth records can't be proxied). SES auto-verifies the
domain once they resolve.

### Gmail forwarding

1. In the inbox that receives your CSA emails: **Settings → Forwarding and POP/IMAP → Add a
   forwarding address** → `plan@csa.example.com`. Google sends a confirmation code, which lands
   in your `raw-emails/` S3 prefix — fetch it from there and enter it.
2. Create a filter: from your CSA's sender → **Forward to** `plan@csa.example.com`.

> Google Workspace admins may need to allow external auto-forwarding (Admin console → Gmail →
> End User Access).

## Recipe corpus

The corpus is a JSON array of recipe objects, stored at `s3://<bucket>/corpus/recipes_tagged.json`
and loaded (and cached) by the Lambda. Each record:

```json
{
  "title": "Teriyaki Salmon",
  "recipe_url": "https://…",          // also the stable id (normalized) for no-repeat
  "pin_url": "https://…",             // optional; omit/empty for non-Pinterest recipes
  "recipe_image": "https://…",        // optional; hotlinked in the email
  "ingredients": ["1 lb salmon", "…"],
  "veggies": ["scallion"],            // canonical, from recipe_tagging
  "protein": "fish",                  // chicken/beef/pork/fish/seafood/lamb/turkey/tofu/vegetarian
  "is_pasta": false,
  "dish_type": "main",                // "main" (a dinner) or "side" (an accompaniment)
  "status": "enriched"
}
```

### Adding recipes

```bash
# one URL per line in a file (blank lines and #comments ignored)
python scripts/add_recipes.py --urls urls.txt --corpus recipes_tagged.json
# then re-upload (no redeploy needed):
aws s3 cp recipes_tagged.json s3://<bucket>/corpus/recipes_tagged.json
```

`add_recipes.py` scrapes title/ingredients/image (via `recipe-scrapers`, with JSON-LD and
microdata fallbacks), tags `veggies`/`protein`/`is_pasta`/`dish_type`, and merges into the
corpus (deduped by URL; failures go to `add_failures.csv`). Requires `pip install
recipe-scrapers requests`. JavaScript-rendered recipe pages can't be scraped statically —
those need a headless browser.

`dish_type` is the fuzziest tag — roasted/glazed/sautéed dishes tag as **sides**, which is
usually right (they're accompaniments, not dinners). Hand-correct it in the JSON if you
disagree.

## Configuration

Non-secret config lives in **SSM Parameter Store** under `/csa-wrangler/` (env vars override it,
which makes local runs easy):

| Parameter | Default | Meaning |
|---|---|---|
| `RECIPIENT_EMAIL` | — | where the plan and alerts are sent |
| `FROM_EMAIL` | — | verified `From:` (e.g. `plan@csa.example.com`) |
| `NIGHTS_PER_WEEK` | `6` | dinners to plan |
| `NO_REPEAT_WEEKS` | `3` | weeks a recipe must sit out before reuse |
| `CORPUS_S3_KEY` | `corpus/recipes_tagged.json` | corpus object key |

## Local development

```bash
python3.13 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest            # corpus-backed tests skip if the corpus is absent

# point the corpus-backed tests at a local corpus
CSA_CORPUS=/path/to/recipes_tagged.json .venv/bin/python -m pytest

# render a .eml to HTML locally (no AWS), to eyeball a plan
PYTHONPATH=src python -m planner.app path/to/email.eml recipes_tagged.json out.html
```

The parser is validated against real CSA emails, the planner against a full-season simulation
(coverage, protein caps, no-repeat invariants), and the renderer against grocery-categorizer
edge cases.

## Operations

- **Idempotency** — SES/Lambda can retry; processed message ids are stored (with TTL) and
  re-deliveries are no-ops, so you never get a duplicate plan.
- **Diagnostics** — a non-CSA email (no `Share contents:` line) gets a brief "couldn't read
  this" reply and is otherwise ignored (no alarm).
- **Failures** — unexpected errors send a diagnostic, land in the SQS DLQ, and raise the
  `csa-wrangler-planner-errors` CloudWatch alarm → SNS email.
- **Updating recipes** — re-run `add_recipes.py` and re-upload the JSON; no redeploy.
- **Cost** — effectively a few cents/month (per-event Lambda, on-demand DynamoDB, minimal S3/SES).

## License

MIT — see [LICENSE](LICENSE).
