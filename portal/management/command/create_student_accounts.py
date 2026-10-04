from django.core.management.base import BaseCommand

from portal.models import Student
from portal.services import ensure_student_accounts


class Command(BaseCommand):
    help = "Create a login (matric number + surname) for every student who has none yet."

    def handle(self, *args, **options):
        missing = Student.objects.filter(user__isnull=True)
        no_surname = list(missing.filter(surname="").values_list("matric_number", flat=True))
        count = ensure_student_accounts(list(missing.values_list("matric_number", flat=True)))
        self.stdout.write(self.style.SUCCESS(f"Created {count} account(s)."))
        if no_surname:
            self.stdout.write(self.style.WARNING(
                f"{len(no_surname)} student(s) have no surname, so they cannot log in for the "
                f"first time until one is added (re-upload the students file with a surname column)."))