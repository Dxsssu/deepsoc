---
id: proc-vpn-abuse-md
title: VPN Abuse Network Traceback SOP
alert_type: vpn abuse
aliases:
  - suspicious vpn login
  - vpn misuse
  - remote access abuse
tags:
  - vpn
  - remote access
  - network traceback
---

# VPN Abuse Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on remote-access logs, alert streams, asset context, and threat intelligence. It should not assume endpoint collection from the remote user device.

## Investigation Workflow

- Confirm the VPN login time, source IP, user account, geolocation clues, device identity if available, and session outcome.
- Pivot on the source IP and account to determine whether the same remote-access pattern touched additional services or repeated identities.
- Query account and asset context to determine whether the user, department, and access pattern are expected.
- Query threat intelligence for the source IP to assess whether it belongs to VPN exit nodes, anonymization services, or malicious infrastructure.
- Correlate the VPN event with suspicious login, lateral movement, or outbound anomalies to determine whether the session became a broader intrusion path.

## Logs to Check

- `security-auditlog*` for remote-access session events, authentication outcomes, and access-control decisions
- `wazuh-alerts*` for suspicious VPN, authentication, and post-access security alerts
- `wazuh-monitoring*` if needed to confirm that telemetry from the VPN gateway or related agents was healthy

## Asset and Intelligence Queries

- Query account ownership, user role, and expected remote-access behavior
- Query target-network segment or asset profile exposed through the VPN connection
- Query source-IP reputation and previous internal observations

## Captain Task Tree Initialization

- Create a branch for VPN session timeline reconstruction
- Create a branch for source-IP and account pivoting across remote-access surfaces
- Create a branch for user-role and asset-context review
- Create a branch for post-session security-event correlation

## Decision Focus

- Is the VPN session expected for the user and source network?
- Did the session lead to unusual internal access behavior?
- Does the source-IP reputation materially raise malicious-confidence?
