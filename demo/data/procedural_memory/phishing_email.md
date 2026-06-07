---
id: proc-phishing-email-md
title: Phishing Email Network Traceback SOP
alert_type: phishing email
aliases:
  - phishing
  - malicious email
  - email lure
tags:
  - email
  - phishing
  - network traceback
---

# Phishing Email Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume mailbox forensics beyond what is visible in available logs.

## Investigation Workflow

- Confirm the suspicious email event time, recipient, sender domain, sender IP if available, and the related alert indicators.
- Pivot on the sender infrastructure, subject line, or campaign indicators to determine whether multiple users or business units received the same lure.
- Query asset and identity context for impacted recipients to determine whether they are high-value targets or tied to sensitive business functions.
- Query threat intelligence on sender domains, URLs, attachment hashes, and sending infrastructure to assess malicious reputation and known campaign links.
- Correlate the phishing event with suspicious login, credential abuse, or outbound traffic alerts to determine whether the lure progressed into follow-on activity.

## Logs to Check

- `wazuh-alerts*` for phishing detections, suspicious URL or attachment alerts, and campaign-related IOC matches
- `security-auditlog*` for related mailbox access, account authentication, or administrative actions around the event window
- `wazuh-archives*` if populated later for fuller raw-event reconstruction

## Asset and Intelligence Queries

- Query recipient identity role, department, and privilege level
- Query malicious domain, URL, and attachment reputation through threat intelligence
- Query whether the same indicators were seen against additional users or systems

## Captain Task Tree Initialization

- Create a branch for campaign spread analysis across recipients and sender indicators
- Create a branch for recipient identity context and risk review
- Create a branch for threat-intelligence enrichment on domains, URLs, and attachments
- Create a branch for follow-on compromise correlation with authentication and outbound alerts

## Decision Focus

- Is this a single-user phishing event or a wider campaign?
- Are the targeted recipients high-value or sensitive accounts?
- Is there evidence that the phishing event progressed into credential theft or compromise?
