---
id: proc-ssh-brute-force-md
title: SSH Brute Force Network Traceback SOP
alert_type: ssh brute force
aliases:
  - brute force
  - ssh password spraying
  - ssh login attack
tags:
  - authentication
  - ssh
  - network traceback
---

# SSH Brute Force Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume host forensics or local shell artifact analysis.

## Investigation Workflow

- Confirm the attack window, source IP, target host, destination port, targeted accounts, and whether any attempt transitioned from failure to success.
- Pivot on the source IP to identify whether it targeted additional SSH-exposed assets or adjacent remote access services such as VPN or bastion portals.
- Query asset context for the impacted host to determine business role, internet exposure, owner, and whether SSH exposure is expected.
- Query threat intelligence for the source IP to determine whether it is associated with scanners, botnets, anonymization nodes, or credential-attack infrastructure.
- Correlate the event with other network-visible alerts to decide whether the activity is isolated brute force or part of a broader intrusion campaign.

## Logs to Check

- `security-auditlog*` for failed and successful login records, target accounts, and remote-access audit trails
- `wazuh-alerts*` for brute-force detections, authentication anomalies, and related exposure alerts
- `wazuh-archives*` if populated later for raw-event reconstruction across the same time window
- `wazuh-monitoring*` only to confirm telemetry coverage and agent health during the attack period

## Asset and Intelligence Queries

- Query asset inventory for the target host, owner, exposure profile, and criticality
- Query whether the same SSH service pattern exists on similar hosts
- Query source-IP threat intelligence and historical internal observations

## Captain Task Tree Initialization

- Create a branch for authentication timeline reconstruction from `security-auditlog*`
- Create a branch for source-IP pivoting across SSH and other remote-access surfaces
- Create a branch for target-host context review using asset inventory
- Create a branch for threat-intelligence enrichment and confidence scoring

## Decision Focus

- Was the activity limited to failed attempts, or did any account authenticate successfully?
- Did the source pivot to multiple hosts or services?
- Does the source or target context raise confidence that this is active malicious intrusion behavior?
