# Methodology Lessons: False Positive Prevention in IDOR Testing

**Date:** April 14, 2026  
**Context:** Cross-Organization IDOR Testing on SAIA QA

---

## The False Positive

### What Happened
Initial test script reported "CRITICAL: Cross-org IDOR" when account B could "access" Ethical Hacking org resources via `/tokens?organizationId=01047551...`.

### Root Cause
The test used **Bearer token authentication** on a **console endpoint** (requires GeneXus cookies), received a **302 redirect to login**, followed the redirect, got **HTTP 200** (login page), and reported it as "unauthorized access."

---

## Three Critical Mistakes

### Mistake #1: Mixed Authentication Schemes

**What I Did Wrong:**
```python
# ❌ WRONG: Using Bearer token on console endpoint
headers = {"Authorization": f"Bearer {TOKEN}"}
response = requests.get("https://console.qa.saia.ai/tokens", headers=headers)
# Returns: 302 Redirect to /login (unauthenticated request)
```

**Why This Failed:**
- Console endpoints authenticate via GeneXus session cookies
- Bearer tokens are for API endpoints
- When Bearer fails → automatic redirect to login
- The redirect itself returns HTTP 200 (login page is valid)

**Correct Approach:**
```python
# ✅ CORRECT: Match auth scheme to endpoint type

# For console.qa.saia.ai endpoints:
console_headers = {
    "Cookie": "JSESSIONID=...; GX_SESSION_ID=..."
}

# For api.qa.saia.ai endpoints:
api_headers = {
    "Authorization": "Bearer {...token...}"
}

# NEVER MIX: Don't use Bearer on console, don't use cookies on API
```

### Mistake #2: Following Redirects Blindly

**What I Did Wrong:**
```python
# ❌ WRONG: Implicit redirect following
response = requests.get(url)  # Default: allow_redirects=True
# 302 /login → follows automatically → lands on 200 OK login page
# Script sees "200 OK" and says "ACCESSIBLE"
```

**Why This Failed:**
- The requests library follows redirects by default
- You don't see the intermediate 302 redirect
- You analyze the FINAL response, not the redirect chain
- Login page returns 200 OK (valid HTML), misinterpreted as "authorization bypass"

**Correct Approach:**
```python
# ✅ CORRECT: Explicit redirect control
response = requests.get(url, allow_redirects=False)

# Check status FIRST
if response.status_code == 302:
    print(f"Redirect to: {response.headers['Location']}")
    print("This means unauthenticated request, not access granted")
    
elif response.status_code == 200:
    # NOW check actual content
    if "login" in response.text.lower():
        print("False positive: This is a login page, not resource data")
    else:
        print("Actual data received")
```

### Mistake #3: HTTP Status ≠ Authorization Result

**What I Did Wrong:**
```python
# ❌ WRONG: Assuming HTTP 200 = "access granted"
if response.status_code == 200:
    verdict = "ACCESSIBLE"  # ← Dangerously simplistic
```

**Why This Failed:**
- HTTP 200 just means "valid response"
- Could be success, error page, login page, rate limit notice, etc.
- Must validate **response content** to confirm what you received

**Correct Approach:**
```python
# ✅ CORRECT: Multi-layer validation

if response.status_code == 200:
    content_type = response.headers.get('content-type', '')
    
    if 'application/json' in content_type:
        try:
            data = response.json()
            # Check if data contains expected fields
            if 'tokens' in data or 'organizationId' in str(data):
                verdict = "ACCESSIBLE - Confirmed data match"
            else:
                verdict = "AMBIGUOUS - JSON but unexpected structure"
        except:
            verdict = "AMBIGUOUS - Invalid JSON"
    
    elif 'text/html' in content_type:
        # Check for login indicators
        if any(x in response.text.lower() for x in ['login', 'sign in', 'password']):
            verdict = "BLOCKED - This is a login page, not resource data"
        else:
            verdict = "AMBIGUOUS - HTML but not login, needs analysis"
    
    else:
        verdict = f"AMBIGUOUS - Unknown content type: {content_type}"

elif response.status_code in [401, 403, 404]:
    verdict = "BLOCKED - As expected for unauthorized access"

elif response.status_code == 302:
    verdict = "REQUIRES_AUTH - Redirect means session needed"
```

---

## Complete IDOR Test Pattern (Corrected)

```python
def test_cross_org_idor(user_token, source_org_id, target_org_id):
    """
    Proper IDOR testing pattern
    """
    
    # 1. AUTHENTICATION: Use correct scheme per endpoint type
    if endpoint.startswith("api.qa.saia.ai"):
        headers = {"Authorization": f"Bearer {user_token}"}
    else:  # console endpoints
        headers = {"Cookie": f"JSESSIONID={session_id}; GX_SESSION_ID={gx_session}"}
    
    # 2. REQUEST: Disable auto-redirect, don't follow blindly
    response = requests.get(
        f"{endpoint}?organizationId={target_org_id}",
        headers=headers,
        allow_redirects=False  # ← CRITICAL
    )
    
    # 3. FIRST CHECK: HTTP status
    if response.status_code == 404:
        return "BLOCKED"  # Endpoint doesn't exist or org not found
    
    if response.status_code == 403:
        return "BLOCKED"  # Explicitly forbidden
    
    if response.status_code == 401:
        return "BLOCKED"  # Unauthorized
    
    if response.status_code == 302:
        # Redirect to login means session required
        location = response.headers.get('Location', '')
        if 'login' in location.lower():
            return "BLOCKED"  # Redirected away from resource
        else:
            return "AMBIGUOUS"  # Unknown redirect target
    
    if response.status_code == 200:
        # NOW we check content, not before
        return validate_response_content(response, target_org_id)
    
    return "UNKNOWN"

def validate_response_content(response, expected_org_id):
    """
    Verify response actually contains the target org's data
    """
    content_type = response.headers.get('content-type', '')
    
    if 'application/json' in content_type:
        data = response.json()
        
        # Does JSON contain org-specific data?
        if 'organizationId' in str(data) and expected_org_id in str(data):
            return "ACCESSIBLE - ORG DATA FOUND"
        elif 'projects' in data and len(data['projects']) > 0:
            return "ACCESSIBLE - ORG PROJECTS FOUND"
        else:
            return "AMBIGUOUS - JSON but no org data"
    
    if 'text/html' in content_type:
        text_lower = response.text.lower()
        
        # Is it a login page?
        if any(x in text_lower for x in ['login', 'sign in', 'password field']):
            return "BLOCKED - FALSE POSITIVE: Login page returned as 200"
        
        # Does it have the expected content?
        if expected_org_id in response.text:
            return "ACCESSIBLE - ORG ID IN HTML"
        
        if 'project api token' in text_lower:
            return "ACCESSIBLE - TOKENS PAGE LOADED"
        
        return "AMBIGUOUS - HTML but content unclear"
    
    return "AMBIGUOUS - Unknown content type"
```

---

## Checklist for Future IDOR Testing

- [ ] **Auth Scheme First** — Determine correct authentication for each endpoint
  - Console endpoints: GeneXus cookies
  - API endpoints: Bearer token (or other)
  - Never mix schemes
  
- [ ] **Disable Auto-Redirect** — Use `allow_redirects=False` in all test tools
  - curl: `-L` flag OFF
  - Python requests: `allow_redirects=False`
  - Postman: Disable "Follow redirects" option
  
- [ ] **Check Status First** — Log HTTP status before analyzing body
  - 401/403/404 = Blocked (good)
  - 302 = Redirect (check target)
  - 200 = Check content (next step)
  
- [ ] **Validate Content** — Never assume 200 OK means data access
  - JSON: Check for expected fields
  - HTML: Check for login/error pages
  - Missing expected fields = ambiguous, not successful
  
- [ ] **Log Everything** — Save full requests/responses
  - HTTP status
  - All headers (auth scheme matters)
  - Full response body
  - Any error messages
  
- [ ] **Compare Baselines** — Test both same-org and cross-org
  - Same org: Should succeed (200 with data)
  - Cross org: Should fail (403/404/redirect)
  - Mismatch = Potential IDOR
  
- [ ] **Test Edge Cases**
  - Empty org ID: `?organizationId=`
  - Fake org ID: `?organizationId=ffffffff-...`
  - Null org ID: `?organizationId=null`
  - Multiple params: `?organizationId=A&organizationId=B`

---

## Key Takeaway

**HTTP status code ≠ Authorization result**

A response with HTTP 200 can be:
- ✅ Successful data access
- ❌ Login page (redirect followed)
- ❌ Error page (server returned status 200 with error HTML)
- ❓ Ambiguous content (need manual analysis)

Always validate **response content**, not just the HTTP status code.

---

## Confidence Calibration

**Before this lesson:**
- If test script says "ACCESSIBLE" → I believed it (false confidence)

**After this lesson:**
- Verify with manual curl/Postman before reporting
- Check response headers (Content-Type matters)
- Read actual response body (not just grep for keywords)
- Reproduce with multiple tools (requests, curl, browser)
- Only report finding if multiple validation methods agree

---

**This lesson applies to all authorization testing, not just IDOR.**
