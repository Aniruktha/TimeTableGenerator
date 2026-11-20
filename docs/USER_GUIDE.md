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

Press **Publish…**, give it a title people will recognise (for example
"Semester 2 · from 12 Jan") and an optional note. It goes live at once.
Students and teachers only ever see the live version.

**Published** lists every version. **Make live** rolls back to an older one,
and **Take it down** hides the timetable without deleting anything. **Change it
in Build** reopens a version so a mid-term change moves as little as possible.
**Print every sheet** gives the whole department's sheets (every class,
teacher and room) ready to print or save as PDF.

### Give people logins — People

- **Add every teacher** creates a login for each teacher in the live timetable
  who doesn't have one yet.
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
