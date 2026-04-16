# Validation Quick Reference Card

**Use this before reporting ANY finding. Print and keep nearby.**

---

## The Three Validation Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 1: RESPONSE-LEVEL (Surface)                               │
│ Does API return expected status and structure?                  │
│ ✓ HTTP 200?  ✓ Data present?  ✓ Format correct?               │
│ → Proves: Endpoint exists                                       │
│ → Does NOT prove: Exploitation or access control bypass        │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 2: METADATA-LEVEL (Critical) ⚠️ DON'T SKIP THIS          │
│ Whose data is in the response?                                  │
│ Extract: owner_id, org_id, avatar_url, embedded_paths          │
│ Compare: response metadata vs. request params                   │
│ → Proves: Whether API filters by user/param                    │
│ → Does NOT prove: Cross-account access impossible              │
└─────────────────────────────────────────────────────────────────┘
                            ↓
     ┌──────────────────────────────────────┐
     │ Is this an AUTHORIZATION finding?    │
     │ (BOLA, IDOR, privilege escalation)   │
     └──────────────────────────────────────┘
         ↙ YES                    ↖ NO
        /                          \
       /                            \
    GO TO LAYER 3              STOP HERE
  (REQUIRED)                  (Layers 1-2 sufficient)


┌─────────────────────────────────────────────────────────────────┐
│ LAYER 3: MULTI-ACCOUNT (Authorization Proof) — MANDATORY        │
│ Can User B access User A's data?                                │
│ Requires: Two accounts from different org/role                 │
│ Test: User A reads A's data ✓                                  │
│       User A reads B's data → Empty/403? ✓                     │
│       User B reads B's data ✓                                   │
│       User B reads A's data → Empty/403? ✓                     │
│ → Proves: Authorization boundary works or fails                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Finding Type → Required Layers

| Finding Type | L1 | L2 | L3 |
|---|---|---|---|
| BOLA / IDOR | ✓ | ✓ | ✓ MANDATORY |
| Privilege Escalation | ✓ | ✓ | ✓ MANDATORY |
| XSS / Injection | ✓ | ✓ | - (browser test instead) |
| SQL Injection | ✓ | ✓ | - |
| Race Condition | ✓ | ✓ | - (state verification) |
| Information Disclosure | ✓ | ✓ | ✓ if cross-account |
| Authentication Bypass | ✓ | ✓ | ✓ MANDATORY |

---

## Metadata to Extract

**For each response, always extract:**

```bash
# Pattern 1: URLs with org/user/project IDs
avatar_url, resource_path, file_url, api_endpoint

# Pattern 2: JSON ownership fields
owner, organizationId, createdBy, creator, userId, tenantId

# Pattern 3: HTTP headers
X-Org-Id, X-User-Id, X-Owner, Set-Cookie (auth tokens)

# Pattern 4: Response envelope
_metadata, context, user, organization, permissions

# Pattern 5: Timestamps + creator
createdAt + createdBy, modifiedAt + modifiedBy
```

**Compare against:**
- Authenticated user ID
- Authenticated org ID
- Request parameters (orgId in URL? userId in param?)

---

## The Three Validation Mistakes

### ❌ Mistake 1: Trust Only HTTP Status

```bash
curl /api/resource/{random-id} → 200 OK
# ❌ Conclusion: "Can access arbitrary resources"
# ✓ Actually: Maybe got error embedded in 200 response
#            Or maybe got authenticated user's data (safe)
#            Or maybe got random data (dangerous)
# FIX: Extract metadata from 200 response
```

### ❌ Mistake 2: Assume URL Params Control Access

```bash
GET /api/agents/{orgId}/{projectId}
# ❌ Assumption: Different orgIds = different data
# ✓ Actually: orgId might be ignored; filter might be user-level
# FIX: Extract orgId from response, compare against request
```

### ❌ Mistake 3: Single Account = Sufficient for Auth Testing

```bash
User A:
  GET /api/agents/{random-uuid} → 200, data returned
# ❌ Conclusion: "BOLA vulnerability"
# ✓ Actually: Just proved you can access your own data
#            Didn't prove you can access others' data
# FIX: Test with User B to confirm cross-org access
```

---

## Before Reporting: The Checklist

```
[ ] Layer 1 completed? (Response status / structure)
    If NO → Mark finding UNCONFIRMED

[ ] Layer 2 completed? (Extracted metadata)
    If NO → Mark finding UNCONFIRMED

[ ] Layer 2 analysis done? (Metadata vs. request comparison)
    If metadata doesn't match request → FALSE POSITIVE
    If metadata matches request → Continue to Layer 3 (if auth finding)

[ ] For AUTH findings: Layer 3 completed or documented as unavailable?
    If NO Layer 3 and no second account available → Mark UNCONFIRMED
    If Layer 3 failed (access denied) → Finding is VALID ✓
    If Layer 3 passed (cross-account access worked) → Finding is CRITICAL ✓

[ ] Dependencies documented?
    Does this finding depend on another unconfirmed finding?
    If YES → Mark both as CONTINGENT

[ ] Reported verdict is explicit?
    □ VALIDATED (all layers passed)
    □ LIKELY VALID (layers 1-2 passed, layer 3 pending)
    □ UNCONFIRMED (layer 3 required, not done)
    □ FALSE POSITIVE (metadata proves safe)
```

---

## Verdict Matrix

| Layer 1 | Layer 2 | Layer 3 | Finding Type | Verdict |
|---------|---------|---------|---|---|
| ✓ Pass | ✓ Pass | N/A | Injection | VALIDATED |
| ✓ Pass | ✓ Pass | ✗ Not done | Auth | UNCONFIRMED |
| ✓ Pass | ✓ Pass | ✓ Pass (denied) | Auth | FALSE POSITIVE |
| ✓ Pass | ✓ Pass | ✓ Pass (allowed) | Auth | VALIDATED / CRITICAL |
| ✓ Pass | ✗ Pass (metadata mismatch) | - | Any | FALSE POSITIVE |
| ✗ Fail | - | - | Any | INVALID |

---

## Real Example: SAIA F-131

| Test | Layer | Result | Analysis |
|------|-------|--------|----------|
| GET /api/agents/{YOUR_ORG} | 1 | 200, 24 agents ✓ | Endpoint works |
| Extract org from avatar URL | 2 | org=YOUR_ORG ✓ | Correct owner |
| GET /api/agents/{RANDOM_ORG} | 1 | 200, 24 agents ✓ | Endpoint works |
| Extract org from avatar URL | 2 | org=YOUR_ORG ❌ | Got your data, not random |
| Conclusion | - | FALSE POSITIVE | API ignores URL param |

---

## The One Rule

**Before marking finding VALIDATED:**

**Ask yourself:** "If I show this evidence to the client's security team, would they agree it's a real vulnerability?"

- If YES → Report it
- If NO → Mark UNCONFIRMED and explain why

---

## Reporting Template (Copy-Paste Ready)

```markdown
## Finding: [Name]

### Validation Status

- [x] Layer 1 (Response): HTTP 200, data returned
- [x] Layer 2 (Metadata): Extracted {field}, compared against request
- [ ] Layer 3 (Multi-Account): Not completed (single account limitation)

### Evidence

**Request:** GET /api/{endpoint}/{param}
**Response Status:** 200
**Response Metadata:** {extracted_field}={value}
**Request Parameter:** {param}={requested_value}

**Comparison:** {match? / mismatch?}

### Verdict

- [ ] VALIDATED
- [ ] LIKELY VALID
- [x] UNCONFIRMED - Requires Layer 3 (multi-account testing)
- [ ] FALSE POSITIVE

### Limitation

Single-account testing only. For definitive authorization validation, recommend client tests with second account.
```

---

## Skills to Load

**For BOLA/IDOR testing:**
```bash
skill_view("pentest-bola-validation-methodology")
```

**For any finding validation:**
```bash
skill_view("pentest-validation-precision-methodology")
```

---

## Remember

> **"A false positive is worse than a missed finding."**
> 
> It wastes the client's time, erodes trust in your work, and trains people to ignore your reports.
> 
> Always validate at the data level, not the response level.
> 
> **Metadata is ground truth. Status codes are noise.**

---

**Last Updated:** 2025-04-13  
**Reference:** SAIA Enterprise AI Pentest, F-131 False Positive Revalidation
