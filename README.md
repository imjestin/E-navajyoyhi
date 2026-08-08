# E-Navajyothi

College management system: attendance, marks, leave, feedback and notices, with
a public college site and a role-based portal for administrators, staff and
students.

- **Installing it on a server?** See [INSTALL.md](INSTALL.md) — Docker, one
  command, then a first-run setup wizard.
- **Working on the code?** See the development section at the end of
  [INSTALL.md](INSTALL.md).

## What it does

| Role | Can do |
|---|---|
| Administrator | Departments, courses, subjects, batches; add or bulk-import students and staff; attendance registers and exports; leave and feedback decisions; notices; institution settings and branding |
| Staff | Take and correct attendance for their own subjects, enter marks, apply for leave, send feedback, view notices |
| Student | See their own attendance and marks, apply for leave, send feedback, view notices |

Built with Django 5.2, PostgreSQL and no front-end build step.
