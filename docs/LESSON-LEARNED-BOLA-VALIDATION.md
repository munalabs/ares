# Lesson Learned: BOLA Vulnerability Validation Methodology

**Date:** 2025-04-13  
**Engagement:** SAIA Enterprise AI Penetration Test  
**Critical Finding:** F-131 False Positive Exposed Validation Gap

---

## Executive Summary

During deep re-validation of BOLA findings, we discovered that **F-131 (Cross-Org Enumeration) and ~8 related findings were false positives**. The root cause: insufficient validation methodology. A single account cannot reliably test authorization boundaries.

**Key Lesson:** Trusting URL parameters as access control indicators without multi-account testing leads to cascading false positives.

---

## The Vulnerability: F-131 Initial Hypothesis

**Finding:** Platform-wide BOLA allowing enumeration of 51+ organizations with access to 1,836 resources.

**Initial PoC Logic:**
```bash
# Test with random org UUID
curl -H "Authorization: Bearer TOKEN" \
  https://lab.qa.saia.ai/api/agents/{RANDOM_ORG_UUID}/{PROJECT_ID}

# Observation: Got agents back
# Conclusion: Can enumerate arbitrary orgs
```

**Report:** 51/51 organizations accessible via sequential UUID enumeration.

---

## The False Positive Trap

### What We Did (Wrong)

1. ✅ Found that `/api/agents/{orgId}/{projectId}` accepts any org UUID
2. ✅ Discovered it returns 24 agents for EVERY org UUID tested
3. ✅ Counted 51 responsive orgs
4. ❌ **ASSUMED:** Different org UUIDs = access to different org data
5. ❌ **FAILED TO VERIFY:** Which org ID the returned data actually belonged to

### What We Missed

The response included avatar URLs like:
```
https://s3.../filesAssistants/01047551-8b5d-44e0-9b7c-c49c80779a38/...
```

**Critical Evidence We Ignored:**
- Your org ID: `01047551-8b5d-44e0-9b7c-c49c80779a38`
- Random org passed in URL: `59da2333-63ee-471b-ac14-1b82e07082d5`
- Org ID in avatar URL: `01047551-8b5d-44e0-9b7c-c49c80779a38` (YOUR org)

**5/5 tests with different random UUIDs all returned avatar URLs containing YOUR org ID, not the random org.**

### The Validation Failure

❌ We validated: "GET returns data"  
❌ We validated: "Response contains 24 agents"  
✅ We validated: "Same agents return regardless of org UUID"  
**✗ We FAILED TO VALIDATE:** "Data belongs to the org UUID we passed"

---

## How to Validate BOLA Correctly

### The Three-Stage Validation Matrix

#### Stage 1: Resource Isolation (Single Account)
```
Test: Same user, DIFFERENT resources from their own org
✓ Can read resource A
✓ Can read resource B
✓ Verify both belong to user's org (check metadata, ownership fields, URLs)
```

**What We Did:** ✅  
**What It Proved:** User can read their own org's data

#### Stage 2: Cross-Org Isolation (Single Account)
```
Test: Same user, attempt to access OTHER org via URL param
✓ Try GET /api/agents/{RANDOM_ORG}/{proj}
✓ GET /api/agents/{ANOTHER_RANDOM_ORG}/{proj}
✗ CRITICAL: Examine response headers, URLs, metadata to confirm WHICH org data was returned
```

**What We Did:** ⚠️ Partial  
**What We Missed:** Avatar URL analysis — we saw it but didn't compare it to the org UUID in the request

**The Fix:**
```bash
# For each response, extract org ID from metadata
RESPONSE=$(curl ... /api/agents/{RANDOM_ORG}/{proj})

# Parse avatar URL
ORG_IN_RESPONSE=$(echo $RESPONSE | grep -o 'filesAssistants/[^/]*' | cut -d'/' -f2)

# Compare
if [ "$ORG_IN_RESPONSE" != "$RANDOM_ORG" ]; then
  echo "ALERT: Requested org $RANDOM_ORG but got $ORG_IN_RESPONSE"
fi
```

#### Stage 3: Multi-Account Cross-Org (REQUIRED for Authorization)
```
Test: DIFFERENT users, SAME resources
Requires: Two accounts from different organizations

Account A (Org X): Try to access org X's agents
Account B (Org Y): Try to access org X's agents ← THIS IS THE TEST
```

**What We Did:** ✗ Not attempted  
**Why It's Critical:** Single-account testing cannot prove cross-org isolation

**Example That Would Catch F-131:**
```
User A (org: 01047551-8b5d-44e0-9b7c-c49c80779a38):
  GET /api/agents/99999999-9999-9999-9999-999999999999/{proj}
  → Returns 0 agents or 403 error (correct)

User B (org: different):
  GET /api/agents/01047551-8b5d-44e0-9b7c-c49c80779a38/{proj}
  → If returns agents: TRUE BOLA
  → If returns empty/403: FALSE POSITIVE
```

---

## The Cascading Damage

**Findings Built on F-14 (BOLA foundation):**
- F-19: System Prompts via BOLA
- F-23: SerpAPI Key via BOLA
- F-27: Conversations via BOLA
- F-61: Agent Export via BOLA
- F-131: Cross-Org Enumeration

**All of these are likely false positives** if the underlying BOLA (F-14) doesn't exist.

---

## How to Fix (Going Forward)

### Validation Checklist for Authorization Findings

**For EVERY BOLA/IDOR/Privilege Escalation claim:**

- [ ] **Level 1 - Single Account, Own Resources:**
  - Can user read their own data? (baseline)
  - Verify metadata confirms ownership (owner ID, org ID in response)

- [ ] **Level 2 - Single Account, Other Users' Resources (if possible):**
  - Try accessing resource ID belonging to another user (if enumerable)
  - Verify response is empty, 403, or contains DIFFERENT user's metadata

- [ ] **Level 3 - Multi-Account Cross-Org (MANDATORY):**
  - Get second account from DIFFERENT org/role
  - Attempt same request as User A with User B's credentials
  - Verify User B cannot access User A's data

- [ ] **Level 4 - Verify Data Isolation in Response:**
  - Don't just check "response returned data"
  - Verify the response contains data from the CORRECT org/user
  - Check metadata fields: owner ID, org ID, embedded URLs, timestamps
  - Compare with known data from that user

- [ ] **Proof of Concept Must Include:**
  - Two test accounts (if available) OR clear statement "single-account limitation"
  - Request sent (user, org UUID, resource ID)
  - Response received (HTTP status, key fields)
  - Evidence of which user/org the response data belongs to
  - Success/failure criteria explicitly stated

---

## What F-131 Should Have Been (with proper validation)

**Initial Hypothesis:** Platform-wide BOLA

**Stage 1 Test Result:** ✅ Can read agents with any org UUID  
**Stage 2 Test Result:** ⚠️ Data returned, but whose org?  
**Avatar URL Check:** ❌ YOUR org ID, not the random org  
**Stage 3 Test Result:** ⚠️ Not attempted (single account limitation)

**Conclusion:** FALSE POSITIVE - API filters by user, not org param.

**Corrected Finding:** "Confusing API design — {orgId} parameter is decorative, doesn't control data access. Filters by authenticated user (correct behavior, but misleading API design)."

---

## Metrics: Impact on Report

| Category | Before | After | Lost |
|----------|--------|-------|------|
| Critical | 25 | ~17 | 8 |
| High | 36 | ~36 | 0 |
| Medium | 46 | ~46 | 0 |
| Low/Info | 20 | ~20 | 0 |
| **Total** | **127** | **~119** | **~8** |

All 8 lost findings were BOLA-dependent false positives.

---

## Key Takeaways

1. **URL parameters are not access control** — they're just decorators
2. **Avatar URLs, metadata, and embedded IDs are the truth** — verify them
3. **Single-account testing cannot validate authorization** — it only tests confidentiality
4. **Cascading dependencies are dangerous** — if F-14 is false, so are F-19/23/27/61/131
5. **Multi-account testing is non-negotiable** for auth findings before reporting

---

## Updated Validation Protocol (For Future Engagements)

```
For EVERY authorization finding (BOLA/IDOR/privilege escalation):

BEFORE reporting:
  1. Extract and analyze metadata from response (owner IDs, org IDs, URLs)
  2. Compare against request parameters
  3. Verify isolation at data level, not just response level
  4. If single account: Mark as "UNCONFIRMED - Requires multi-account testing"
  5. If cross-org claimed: Use 2nd account before marking VALIDATED

REPORTING:
  - Always note which validation stages were completed
  - Always disclose single-account limitations
  - Always cite metadata evidence (URLs, IDs in response)
```

---

## References

- OWASP API #1: BOLA (Broken Object Level Authorization)
- WSTG-AUTHZ-02: Testing for bypassing authorization schema
- Lesson: Don't trust what the API says — verify what the data says
