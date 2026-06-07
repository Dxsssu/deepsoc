---
id: proc-privilege-escalation-md
title: Privilege Escalation Network Traceback SOP
alert_type: privilege escalation
aliases:
  - suspicious privilege gain
  - elevated access abuse
  - role escalation
tags:
  - privilege escalation
  - identity
  - network traceback
---

# Privilege Escalation Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on audit logs, alert streams, asset context, and vulnerability-state information. It should not assume local process or memory-level validation.

## Investigation Workflow

- Confirm the alert time, impacted account, target system or application context, and the privilege-related action that triggered the alert.
- Pivot on the same account and target system to determine whether related authentication, policy-change, or access-control events occurred nearby.
- Query asset and identity context to understand whether the privilege increase is expected for the user, account type, or application role.
- Query vulnerability or exposure data to determine whether the target surface has known weaknesses that may enable privilege abuse.
- Correlate the event with suspicious login, lateral movement, or malicious outbound alerts to determine whether the privilege gain fits into a larger intrusion sequence.

## Logs to Check

- `security-auditlog*` for role changes, privileged access events, and access-control records
- `wazuh-alerts*` for privilege-related detections, policy-change alerts, and suspicious identity behavior
- `wazuh-states-vulnerabilities*` for weakness context that may align with escalation paths
- `wazuh-archives*` if populated later for broader raw-event reconstruction

## Asset and Intelligence Queries

- Query identity role, entitlement baseline, and ownership
- Query asset or application criticality and privileged-surface exposure
- Query vulnerability-state context for the affected target

## Captain Task Tree Initialization

- Create a branch for privileged-action timeline reconstruction
- Create a branch for account-context and entitlement-baseline review
- Create a branch for target-asset or application exposure review
- Create a branch for campaign correlation with suspicious access or movement events

## Decision Focus

- Is the privilege increase expected for the account or role?
- Did the event coincide with suspicious authentication or lateral movement?
- Does target exposure or weakness context make the privilege gain more credible as malicious?
