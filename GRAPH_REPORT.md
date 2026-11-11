# Graph Report - portal_code  (2026-11-11)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 112 nodes · 392 edges · 10 communities (7 shown, 3 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 3 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9

## God Nodes (most connected - your core abstractions)
1. `get_db()` - 74 edges
2. `login_required()` - 51 edges
3. `log_action()` - 44 edges
4. `admin_chat_emergency_clear()` - 7 edges
5. `admin_edit()` - 7 edges
6. `admin_delete_doc_forever()` - 6 edges
7. `current_user()` - 6 edges
8. `admin_mailing()` - 6 edges
9. `admin_new()` - 6 edges
10. `admin_new_version()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `admin_delete_doc_forever()` --calls--> `log_action()`  [EXTRACTED]
  routes_main.py → utils.py
- `admin_delete_doc_forever()` --references--> `login_required()`  [EXTRACTED]
  routes_main.py → utils.py
- `login()` --calls--> `get_db()`  [EXTRACTED]
  routes_auth.py → utils.py
- `admin_announcement_delete()` --calls--> `get_db()`  [EXTRACTED]
  routes_main.py → utils.py
- `admin_announcement_edit()` --calls--> `get_db()`  [EXTRACTED]
  routes_main.py → utils.py

## Import Cycles
- None detected.

## Communities (10 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.21
Nodes (21): chat_api_chats(), chat_api_default_avatars(), chat_api_group_members(), chat_api_heartbeat(), chat_api_messages(), chat_api_my_avatar(), chat_api_offline(), chat_api_read() (+13 more)

### Community 1 - "Community 1"
Cohesion: 0.16
Nodes (6): inject_user(), login(), logout(), admin_chat_emergency_clear(), close_db(), current_user()

### Community 2 - "Community 2"
Cohesion: 0.14
Nodes (10): admin_department_edit(), admin_departments(), admin_documents(), admin_mailing(), admin_restore(), admin_restore_emp_from_trash(), admin_trash_documents(), admin_type_delete() (+2 more)

### Community 3 - "Community 3"
Cohesion: 0.14
Nodes (14): admin_announcement_edit(), admin_announcement_new(), admin_archive(), admin_chat_management(), admin_delete_emp_forever(), admin_direction_delete(), admin_direction_edit(), admin_directions() (+6 more)

### Community 4 - "Community 4"
Cohesion: 0.15
Nodes (12): admin_announcement_delete(), admin_chat_group_delete(), admin_chat_groups(), admin_chat_settings(), admin_employee_new(), admin_help(), admin_restore_doc_from_trash(), document() (+4 more)

### Community 5 - "Community 5"
Cohesion: 0.15
Nodes (13): admin_announcements(), admin_chat_group_edit(), admin_delete(), admin_department_delete(), admin_employees(), admin_employees_import(), admin_home(), admin_log() (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.50
Nodes (4): admin_edit(), admin_new(), admin_new_version(), _save_pdf()

## Knowledge Gaps
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_db()` connect `Community 0` to `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.337) - this node is a cross-community bridge._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Why does `login_required()` connect `Community 5` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 6`, `Community 8`, `Community 9`?**
  _High betweenness centrality (0.085) - this node is a cross-community bridge._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Why does `log_action()` connect `Community 3` to `Community 0`, `Community 1`, `Community 2`, `Community 4`, `Community 5`, `Community 6`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._