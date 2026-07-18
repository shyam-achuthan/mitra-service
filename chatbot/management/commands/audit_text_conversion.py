"""
Django management command to audit text_conversion_type configuration across state machines.

This gives config-drift visibility on demand (issue 8): organisation and person names must be
transliterated, not translated, but the per-step text_conversion_type field defaults to TRANSLATE
and drifted silently in PROD ("Mahila Samakhya" -> "Female Organisation"). Run this to see, per bot
and step, how each step is configured and to flag identity/personal-info steps that are set to
TRANSLATE. It is strictly read-only and mutates nothing.

Usage:
    python manage.py audit_text_conversion
    python manage.py audit_text_conversion --bot-route /shikshalokam_chaupal
    python manage.py audit_text_conversion --only-warnings
"""
from django.core.management.base import BaseCommand

from chatbot.models.company_models import CompanyStateMachine
from chatbot.models.enums import TextConversionType

IDENTITY_MARKERS = ('personal_info', 'personal', 'name', 'organization', 'organisation',
                    'location', 'identity', 'profile')


def _is_identity_step(name):
    step_name = (name or '').lower()
    return any(marker in step_name for marker in IDENTITY_MARKERS)


class Command(BaseCommand):
    help = 'Audit text_conversion_type across CompanyStateMachine steps (read-only).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bot-route', type=str, default=None,
            help='Restrict the audit to a single bot route (CompanyBot.route).',
        )
        parser.add_argument(
            '--only-warnings', action='store_true',
            help='Print only the identity steps configured to TRANSLATE (the likely-misconfigured ones).',
        )

    def handle(self, *args, **options):
        qs = CompanyStateMachine.objects.select_related('company_bot').order_by(
            'company_bot__route', 'step'
        )
        if options['bot_route']:
            qs = qs.filter(company_bot__route=options['bot_route'])

        total = 0
        warnings = 0
        for sm in qs:
            total += 1
            is_translate = sm.text_conversion_type == TextConversionType.TRANSLATE
            flagged = is_translate and _is_identity_step(sm.name)
            if flagged:
                warnings += 1

            if options['only_warnings'] and not flagged:
                continue

            route = sm.company_bot.route if sm.company_bot else '(no bot)'
            marker = ' <-- IDENTITY STEP SET TO TRANSLATE' if flagged else ''
            line = (
                f"route={route} step={sm.step} name={sm.name} "
                f"text_conversion_type={sm.text_conversion_type}{marker}"
            )
            if flagged:
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)

        self.stdout.write('')
        summary = f"Audited {total} state machine(s); {warnings} identity step(s) set to TRANSLATE."
        if warnings:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
