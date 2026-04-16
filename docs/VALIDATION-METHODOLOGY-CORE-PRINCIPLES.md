# Core Principles: Pentest Validation Methodology

**Established:** 2025-04-13  
**Source:** SAIA Enterprise AI Penetration Test, F-131 False Positive Revalidation  
**Status:** Adopted as standard methodology for all future engagements

---

## The Five Core Principles

### 1. **Verify What The Data Says, Not What The API Says**

**Principle:** Response status codes and request acceptance don't prove exploitation. Extract and verify ownership metadata from response payloads.

**Wrong:**
```bash
curl /api/agents/{RANDOM_ORG}/proj → 200 OK, 24 agents returned
# Conclusion: "Can access different org"
```

**Right:**
```bash
curl /api/agents/{RANDOM_ORG}/proj → 200 OK, 24 agents returned
# Extract: avatarImage: .../filesAssistants/{YOUR_ORG}/...
# Conclusion: "Got your org's data when requesting random org"
# Finding: "False positive - API filters by user, not org param"
```

**Pattern:** Always examine response metadata (URLs, owner IDs, org IDs, embedded paths) and compare against request parameters.

---

### 2. **URL Parameters Are Decorators, Not Access Control**

**Principle:** Just because an API accepts a parameter doesn't mean it controls access. Actual access control happens at the data layer (user context, authentication state).

**Example (SAIA):**
```
Endpoint: GET /api/agents/{orgId}/{projectId}
Parameter: {orgId} in URL looks like it selects organization
Reality: Filters by authenticated user's org, ignores {orgId}
```

**Validation:** Extract org ID from response metadata, compare against org ID in URL param. If different → API ignores the parameter.

**Impact:** This single misunderstanding led to F-14, F-19, F-23, F-27, F-61, F-131 false positives.

---

### 3. **Single-Account Testing Cannot Validate Authorization**

**Principle:** One account can prove "I can access my data" but cannot prove "I cannot access others' data."

**What Single Account Tests:**
- ✅ Confidentiality baseline (user can read their own data)
- ✅ Whether API responds to requests
- ✅ Basic endpoint existence

**What Single Account Cannot Test:**
- ❌ Cross-org access control
- ❌ Cross-user authorization
- ❌ Cross-role privilege boundaries

**Rule:** Any authorization finding claimed with single account must be marked **UNCONFIRMED** unless multi-account testing is explicitly documented.

**Reporting:**
```markdown
## Validation Status

- Layer 1 (Response): ✅ Passed
- Layer 2 (Metadata): ✅ Passed
- Layer 3 (Multi-Account): ❌ Not attempted (single account limitation)

**Verdict:** UNCONFIRMED - Requires 2+ accounts for authorization validation
```

---

### 4. **False Positives Cascade; Validate Dependencies First**

**Principle:** A false positive in finding A invalidates all findings that depend on A. Always validate foundational assumptions before reporting derivatives.

**SAIA Example:**
```
F-14 (BOLA foundation)
  ├─ F-15 (Agent deletion via BOLA)
  ├─ F-16 (Non-admin agent creation)
  ├─ F-17 (Non-admin KB creation)
  ├─ F-18 (Non-admin tool creation)
  ├─ F-19 (System prompts via BOLA)
  ├─ F-23 (SerpAPI key via BOLA)
  ├─ F-27 (Conversations via BOLA)
  ├─ F-61 (Agent export via BOLA)
  └─ F-131 (Cross-org enumeration via BOLA)

If F-14 is false → F-15 through F-131 are questionable
```

**Validation Strategy:**
1. Identify foundational claims (BOLA, auth bypass, etc.)
2. Validate foundational claims FIRST with full rigor
3. Only then report derivative findings
4. If foundation falls, retract all dependents

---

### 5. **Metadata Evidence Is Ground Truth; Response Status Is Noise**

**Principle:** HTTP status codes are just signals. The actual proof of vulnerability is in the response metadata.

**Metadata to Extract (varies by API):**
- Owner/creator ID fields
- Organization/tenant ID fields
- Avatar URLs with embedded org/project IDs
- Resource paths containing user/org identifiers
- Timestamps with creator/modifier fields
- Access control indicators (public/private flags)

**Comparison Process:**
```bash
# For each test, extract metadata
REQUEST_ORG="random-uuid"
REQUEST_USER="authenticated-as-user-a"

RESPONSE=$(curl /api/resource/$REQUEST_ORG)
RESPONSE_ORG=$(extract_org_from_metadata $RESPONSE)
RESPONSE_OWNER=$(extract_owner_from_metadata $RESPONSE)

# Compare
if [ $RESPONSE_ORG == $REQUEST_ORG ]; then
  echo "✅ Correct org"
elif [ $RESPONSE_ORG == $YOUR_ORG ]; then
  echo "⚠️ Got your org, not requested org"
elif [ $RESPONSE_OWNER == $REQUEST_USER ]; then
  echo "✅ Owner matches authenticated user"
fi
```

**Why This Matters:**
- 200 status ≠ exploitation
- Data returned ≠ exploitation
- Data OWNERSHIP confirmed ≠ exploitation only if it belongs to attacker
- If data belongs to legitimate owner → Safe behavior (false positive)

---

## The Validation Framework (Applied to Every Finding)

### Layer 1: Response-Level (Surface)
- Does the API return 200?
- Does response contain data/structure?
- **Does NOT prove:** Who owns the data

### Layer 2: Metadata-Level (Critical)
- Extract ownership indicators from response
- Compare against request parameters
- **Proves:** Whether API filters by user or by param

### Layer 3: Multi-Account Boundary (Authorization Proof)
- Use second account from different org/role
- Test cross-boundary access
- **Proves:** Whether authorization actually works

**Rule:** 
- For authorization findings: All 3 layers required
- For injection findings: Layers 1-2 required, plus browser execution
- For logic bugs: Layers 1-2 required, plus state verification
- For infrastructure findings: Layers 1-2 required

---

## Reporting Requirements

Every finding must include:

1. **Which layers were completed:**
   - [ ] Layer 1 (Response): Completed / Not Applicable
   - [ ] Layer 2 (Metadata): Completed / Not Applicable
   - [ ] Layer 3 (Multi-Account): Completed / Not Available / Not Applicable

2. **Metadata evidence extracted:**
   - Request: `GET /api/{param}`
   - Response status: HTTP 200
   - Response metadata: `{extracted_field}: {value}`

3. **Comparison analysis:**
   - Request param value: `{value_a}`
   - Response metadata value: `{value_b}`
   - Match? `{yes/no/unclear}`

4. **Limitations documented:**
   - Single account only? Note it.
   - Multi-account testing unavailable? Note it.
   - Dependent on unconfirmed finding? Cross-reference it.

5. **Verdict:**
   - VALIDATED (all layers passed)
   - LIKELY VALID (layers 1-2 passed, layer 3 pending)
   - UNCONFIRMED (layer 3 required, not completed)
   - FALSE POSITIVE (metadata proves safe behavior)

---

## Pre-Reporting Checklist

**BEFORE marking any finding validated:**

- [ ] Extracted metadata from response (URLs, IDs, ownership fields)
- [ ] Compared metadata against request (matching/mismatching?)
- [ ] For auth findings: Multi-account tested OR limitation documented
- [ ] For injection: Verified context and execution, not just reflection
- [ ] For state changes: Documented before/after state and what changed
- [ ] Dependency check: Does this finding depend on another unconfirmed finding?
- [ ] False positive smell test: Could this be safe behavior being misinterpreted?
- [ ] Cascading check: If this is false, would 5+ other findings become questionable?

**If any box unchecked:** Mark finding as UNCONFIRMED or continue validation, don't report as VALIDATED.

---

## The SAIA F-131 Lesson Summary

**False Positive:** F-131 (51-org BOLA, 1,836 resources exposed)

**How It Happened:**
1. Tested /api/agents with random org UUIDs
2. Got 200 responses with data
3. Didn't examine whose data it was
4. Reported as critical BOLA

**How We Fixed It:**
1. Examined avatar URLs in responses
2. Compared org ID in URL vs. org ID in embedded URL
3. Found all responses had YOUR org, not random org
4. Retracted finding and 7 dependents

**Key Insight:** The evidence was in the response all along. We just didn't look carefully enough.

**Impact:** Prevents 8 false positives from entering client report, preserving credibility.

---

## Implementation

**All new skills must:**
- Incorporate Layer 1-3 validation framework
- Emphasize metadata extraction
- Document multi-account requirements explicitly
- Include real false positive examples
- Require explicit verdict (VALIDATED/UNCONFIRMED/FALSE POSITIVE) before reporting

**All pentest reports must:**
- Include validation status for each finding
- Document which validation layers were completed
- Note single-account limitations explicitly
- Provide metadata evidence in appendix
- Cross-reference dependencies between findings

**All findings with dependencies must:**
- Be marked PENDING if dependency is unconfirmed
- Be retracted if dependency becomes false positive
- Be explicitly linked in report

---

## References

- OWASP API Security Top 10 #1: BOLA
- WSTG Authorization Testing (WSTG-AUTHZ)
- **Lesson Source:** SAIA Enterprise AI Pentest 2025-04-13
- **Skills Updated:** 
  - `pentest-bola-validation-methodology` (v1.1)
  - `pentest-validation-precision-methodology` (v1.0 - new)

---

## Questions for Future Engagements

Before starting authorization testing:

1. **Do we have multiple test accounts?** If not, document limitation upfront.
2. **Can we request second account from client?** If testing multi-tenant system, ask now.
3. **What are the dependency trees?** Which findings depend on others?
4. **What's the metadata structure?** Where are owner/org IDs embedded?
5. **What should we NOT trust?** Which parameters look like access control but aren't?

---

**Adopted by:** Hermes Pentest Agent  
**Effective:** 2025-04-13 forward  
**Review Date:** 2025-09-13 (6-month review)
