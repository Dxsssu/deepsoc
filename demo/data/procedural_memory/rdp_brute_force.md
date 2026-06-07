---
id: proc-rdp-brute-force-md
title: RDP Brute Force Network Traceback SOP
alert_type: rdp brute force
aliases:
  - rdp attack
  - remote desktop brute force
  - rdp password spraying
tags:
  - rdp
  - authentication
  - network traceback
---

# RDP Brute Force Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume Windows host forensic access.

## Investigation Workflow

- Confirm the attack window, source IP, target host, targeted accounts, and whether any RDP attempt became a successful login.
- Pivot on the source IP to determine whether it also touched other RDP-exposed hosts, VPN services, or remote-administration endpoints.
- Query asset context for the target host to determine ownership, exposure, criticality, and whether RDP exposure is expected.
- Query threat intelligence for the source IP to determine whether it is linked to scanning, brute-force infrastructure, or prior malicious reputation.
- Correlate the event with additional suspicious authentication or lateral-movement alerts to determine whether the brute-force activity progressed.

## Logs to Check

- `security-auditlog*` for failed and successful remote login records and account activity
- `wazuh-alerts*` for brute-force, remote-access, and suspicious authentication detections
- `wazuh-archives*` if populated later for raw-event reconstruction
- `wazuh-monitoring*` only to validate telemetry continuity

## Asset and Intelligence Queries

- Query target-host asset context, owner, and remote-access exposure
- Query whether similar RDP exposure exists on additional peer assets
- Query source-IP reputation and historical observations

## Captain Task Tree Initialization

- Create a branch for RDP authentication timeline reconstruction
- Create a branch for source-IP pivoting across remote-access surfaces
- Create a branch for target-host context and exposure review
- Create a branch for post-login progression analysis if any success occurred

## Decision Focus

- Did the activity remain failed-only or transition to successful access?
- Did the same source target multiple RDP-capable systems?
- Does the surrounding context justify escalation beyond opportunistic scanning?
