---
id: proc-account-takeover-md
title: Account Takeover Network Traceback SOP
alert_type: account takeover
aliases:
  - ato
  - account compromise
  - identity takeover
tags:
  - account takeover
  - identity
  - network traceback
---

# Account Takeover Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume direct mailbox or endpoint forensic review.

## Investigation Workflow

- Confirm the impacted account, suspicious session window, source IPs, authentication pattern, and any access-control anomalies.
- Pivot on the account and related source IPs to identify reuse across other services, assets, or identities.
- Query identity context to determine privilege level, business role, expected access behavior, and historical baseline deviations.
- Query threat intelligence for the suspicious source IPs or related external indicators.
- Correlate the event with phishing, suspicious login, VPN abuse, or outbound anomalies to determine how the takeover may have occurred and whether it progressed.

## Logs to Check

- `security-auditlog*` for login success/failure, policy actions, access-control decisions, and related identity activity
- `wazuh-alerts*` for suspicious login, account abuse, and correlated security alerts
- `wazuh-archives*` if populated later for broader raw-event reconstruction

## Asset and Intelligence Queries

- Query account ownership, role, and baseline access expectations
- Query related target assets or applications accessed by the account
- Query suspicious source-IP threat intelligence and prior internal observations

## Captain Task Tree Initialization

- Create a branch for account timeline reconstruction from `security-auditlog*`
- Create a branch for source-IP pivoting across services and identities
- Create a branch for account-risk and accessed-asset context review
- Create a branch for possible initial-access correlation such as phishing or VPN abuse

## Decision Focus

- Does the account show baseline deviation strong enough to confirm takeover risk?
- Did the same source or account touch multiple services or assets?
- Is there enough adjacent evidence to infer likely initial access path?
