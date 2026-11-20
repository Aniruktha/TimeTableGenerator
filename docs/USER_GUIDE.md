# Using Timetable

## Students

1. Open the site, choose **Student**, and sign in with your roll number and
   the temporary password from your class advisor or department office.
2. Choose your own password. Nobody else will know it, including the office.
3. **My timetable** shows today's periods first (the current one is marked
   **Now**), then your subjects with their faculty and rooms, then the whole
   week. In a lab split into batches, each batch's room and teacher are listed.
4. **Print** gives a one-page A4 copy.

Forgot your password? Ask the timetable office to reset it. You'll get a
new temporary password.

## Teachers

Sign in as **Teacher** with the login the timetable office gave you. You see
every period you take: which class, which batch, which room. Today comes
first, then your week. **Print** gives a copy for the staff room. Change your
password any time from the menu under your name.

## The timetable office (administrators)

### Make the timetable — Build

Six steps: **Start → Rooms → Teachers → Classes → Subjects → Generate**. Every
step has a form and a CSV importer with a blank template. **Check for
problems** finds impossible setups before you wait for the solver: a teacher
with more hours than free periods, a class needing more periods than the week
has. After **Generate**, look through the class, teacher and room views. You
can click a period to lock it in place or move it, then re-plan; everything
else stays where it is.

### Publish it

Press **Publish…**, check the department, and give it a title people will
recognise (for example "Semester 2 · from 12 Jan") and an optional note. It
goes live at once for that department. Students and teachers only ever see
live versions.

**Published** lists every version. Each department has its own live version.
**Make live** rolls back to an older one, and **Take it down** hides one
department's timetable without deleting anything. Other departments are not
affected. **Change it
in Build** reopens a version so a mid-term change moves as little as possible.
**Print every sheet** gives the whole department's sheets (every class,
teacher and room) ready to print or save as PDF.

### More than one department

Build the college **one department at a time**. Give each setup its
department under **Start** (the AMCS sample already says `AMCS`), generate
it and publish it. When you generate the next department, any teacher or
room it shares with a department that is already live keeps clear of the
periods it has there. The result page says what it kept clear of, for
example "Kept clear of what AMCS already has live: SUGANTHI S D (3), room
J510 (16)".

- **The same teacher or room must have the same ID in every department.**
  That is how the generator knows two setups mean the same person or room.
  The class IDs must be different in every department.
- A shared teacher's daily limit and longest run of back-to-back periods
  count their periods in every department.
- Departments with different bell timings are matched by clock time: a
  period that overlaps another department's period counts as clashing.
- Publishing, and **Make live** on an older version, are checked against
  the other departments' live timetables as they are at that moment. If
  something clashes, nothing goes live and the message names the teacher,
  the period and the department; generate again so it keeps clear.
- If no timetable can fit around the other departments, the result names
  the cause, for example "SUDHA S S already teaching in AMCS at …".
- A teacher who works in several departments has one login and sees one
  week with all of their periods, each marked with its department.

The `second_dept_demo` sample is a made-up department that shares three
teachers and lab J510 with AMCS. Publish AMCS, then generate it to see this
in action.

### Give people logins — People

- **Add every teacher** creates a login for each teacher in the live
  timetables who doesn't have one yet. A teacher shared by departments gets
  one login.
- **Import students (CSV)** takes a file with the columns `roll_no, name,
  class`. `class` can be the class name or its id; the dialog lists them.
- **+ Add a person** adds a single login of any role.

New logins get a temporary password that is shown **once**. Hand it out, or
use **Download list** to get a CSV to print slips from. Everyone sets their
own password the first time they sign in. From the **•••** menu on a person
you can edit their name or class, reset their password, disable their login,
or delete it.

A login is linked to a teacher or class *in the live timetable*. If a later
version drops that teacher or class, the person's row is flagged "not in live
timetable" and they see a message asking them to contact you.

**Preview** shows the portal exactly as any class or teacher sees it.
