---
name: law-update
description: >
  Update or append to constitutional laws.
  Adds new laws, modifies existing ones, or creates amendments.
  Requires heartbeat verification before application.
  Use when: "add new law", "amend constitution", "update SOUL",
  "new regulation", "law change", "constitutional amendment".
argument-hint: "<law-file> <new-content>"
allowed-tools: ["Read", "Edit", "Write", "Bash"]
---

# Law Update

Add or modify constitutional laws.

## Process

1. **Validate input**
   - Check law format (Markdown with proper structure)
   - Verify no conflicts with existing laws
   - Ensure ID uniqueness

2. **Draft amendment**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/law-draft.py" \
     --file "$1" --content "$2"
   ```

3. **Heartbeat verification**
   - Submit to heartbeat agent for approval
   - Wait for PULSE_H confirmation
   - If rejected: provide reason, allow revision

4. **Apply update**
   - Write to `laws/` directory
   - Rebuild registry
   - Re-seal with Ed25519
   - Log amendment event

5. **Notify all agents**
   - Write LAW_UPDATE event
   - Agents reload on next cycle

## Law Format

```markdown
## LAW_N: LAW_NAME

**Type:** RULE | LIMIT | PROTOCOL | OATH | CONSTRAINT

**Description:**
Clear, unambiguous description.

**Enforcement:**
How this law is checked at runtime.

**Violation:**
Consequence of breaking this law.

**Example:**
```
Good: [example]
Bad: [counter-example]
```
```

## Arguments

- `$1` = law file to update (e.g., `SOUL.md`, `BOUND.md`)
- `$2` = new law content (or use stdin)

## Output

```
LAW UPDATE
==========
File:      laws/SOUL.md
Law ID:    LAW_8
Type:      RULE
Status:    Heartbeat approved ✅
Seal:      Updated (hash: ...)

New law active. Agents notified.
```
