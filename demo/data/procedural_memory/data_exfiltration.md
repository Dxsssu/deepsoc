---
id: proc-data-exfiltration-md
title: Data Exfiltration Network Traceback SOP
alert_type: data exfiltration
aliases:
  - exfiltration
  - bulk data transfer
  - suspicious upload
tags:
  - exfiltration
  - outbound
  - network traceback
---

# Data Exfiltration Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume host-side file forensics or direct content inspection.

## Investigation Workflow

- Confirm the source asset, destination endpoint, protocol, transfer window, and alert characteristics such as volume or rarity.
- Pivot on the destination and time window to determine whether other internal assets communicated with the same endpoint or pattern.
- Query asset context for the source system and associated account to understand business role, expected transfer behavior, and data sensitivity.
- Query threat intelligence for the destination IP, domain, cloud storage endpoint, or hosting provider to assess whether the target is suspicious or known malicious.
- Correlate the exfiltration alert with suspicious login, vulnerability exploitation, or privilege-related detections to determine whether the transfer is part of a larger compromise.

## Logs to Check

- `wazuh-alerts*` for exfiltration alerts, anomalous outbound traffic, and IOC matches
- `security-auditlog*` for identity activity or administrative actions near the transfer window
- `wazuh-archives*` if populated later for broader raw-event correlation

## Asset and Intelligence Queries

- Query source-asset business role, owner, and sensitivity
- Query associated account context and whether similar transfers are expected
- Query destination reputation, infrastructure ownership, and prior malicious associations

## Captain Task Tree Initialization

- Create a branch for transfer timeline reconstruction from `wazuh-alerts*`
- Create a branch for destination pivoting across peer assets
- Create a branch for source-asset and account-context review
- Create a branch for campaign correlation with nearby compromise indicators

## Decision Focus

- Is the transfer size or destination unusual for the source asset?
- Is the destination business-justified or suspicious?
- Does surrounding alert context suggest confirmed data theft risk?
