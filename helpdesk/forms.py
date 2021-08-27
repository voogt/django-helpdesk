"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

forms.py - Definitions of newforms-based forms for creating and maintaining
           tickets.
"""
import logging
from datetime import datetime, date, time

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django import forms
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth import get_user_model
from django.utils import timezone

from helpdesk.lib import safe_template_context, process_attachments
from helpdesk.models import (Ticket, Queue, FollowUp, IgnoreEmail, TicketCC,
                             CustomField, TicketCustomFieldValue, TicketDependency, UserSettings, KBItem)
from helpdesk import settings as helpdesk_settings

logger = logging.getLogger(__name__)
User = get_user_model()

CUSTOMFIELD_TO_FIELD_DICT = {
    # Store the immediate equivalences here
    'boolean': forms.BooleanField,
    'date': forms.DateField,
    'time': forms.TimeField,
    'datetime': forms.DateTimeField,
    'email': forms.EmailField,
    'url': forms.URLField,
    'ipaddress': forms.GenericIPAddressField,
    'slug': forms.SlugField,
}

CUSTOMFIELD_DATE_FORMAT = "%Y-%m-%d"
CUSTOMFIELD_TIME_FORMAT = "%H:%M:%S"
CUSTOMFIELD_DATETIME_FORMAT = f"{CUSTOMFIELD_DATE_FORMAT} {CUSTOMFIELD_TIME_FORMAT}"


class CustomFieldMixin(object):
    """
    Mixin that provides a method to turn CustomFields into an actual field
    """

    def customfield_to_field(self, field, instanceargs):
        # Use TextInput widget by default
        instanceargs['widget'] = forms.TextInput(attrs={'class': 'form-control'})
        # if-elif branches start with special cases
        if field.data_type == 'varchar':
            fieldclass = forms.CharField
            instanceargs['max_length'] = field.max_length
        elif field.data_type == 'text':
            fieldclass = forms.CharField
            instanceargs['widget'] = forms.Textarea(attrs={'class': 'form-control'})
            instanceargs['max_length'] = field.max_length
        elif field.data_type == 'integer':
            fieldclass = forms.IntegerField
            instanceargs['widget'] = forms.NumberInput(attrs={'class': 'form-control'})
        elif field.data_type == 'decimal':
            fieldclass = forms.DecimalField
            instanceargs['decimal_places'] = field.decimal_places
            instanceargs['max_digits'] = field.max_length
            instanceargs['widget'] = forms.NumberInput(attrs={'class': 'form-control'})
        elif field.data_type == 'list':
            fieldclass = forms.ChoiceField
            choices = field.choices_as_array
            if field.empty_selection_list:
                choices.insert(0, ('', '---------'))
            instanceargs['choices'] = choices
            instanceargs['widget'] = forms.Select(attrs={'class': 'form-control'})
        else:
            # Try to use the immediate equivalences dictionary
            try:
                fieldclass = CUSTOMFIELD_TO_FIELD_DICT[field.data_type]
                # Change widgets for the following classes
                if fieldclass == forms.DateField:
                    instanceargs['widget'] = forms.DateInput(attrs={'class': 'form-control date-field'})
                elif fieldclass == forms.DateTimeField:
                    instanceargs['widget'] = forms.DateTimeInput(attrs={'class': 'form-control datetime-field'})
                elif fieldclass == forms.TimeField:
                    instanceargs['widget'] = forms.TimeInput(attrs={'class': 'form-control time-field'})
                elif fieldclass == forms.BooleanField:
                    instanceargs['widget'] = forms.CheckboxInput(attrs={'class': 'form-control'})

            except KeyError:
                # The data_type was not found anywhere
                raise NameError("Unrecognized data_type %s" % field.data_type)

        self.fields['custom_%s' % field.name] = fieldclass(**instanceargs)


class EditTicketForm(CustomFieldMixin, forms.ModelForm):
    class Meta:
        model = Ticket
        exclude = ('created', 'modified', 'status', 'on_hold', 'resolution', 'last_escalation', 'secret_key', 'kbitem')

    class Media:
        js = ('helpdesk/js/init_due_date.js', 'helpdesk/js/init_datetime_classes.js')

    def __init__(self, *args, **kwargs):
        """
        Add any custom fields that are defined to the form
        """
        super(EditTicketForm, self).__init__(*args, **kwargs)

        self.fields['assigned_to'].choices = [(x.pk, x) for x in self.instance.get_assign_to_list()]

        # Disable and add help_text to the merged_to field on this form
        self.fields['merged_to'].disabled = True
        self.fields['merged_to'].help_text = _('This ticket is merged into the selected ticket.')

        for field in CustomField.objects.all():
            initial_value = None
            try:
                current_value = TicketCustomFieldValue.objects.get(ticket=self.instance, field=field)
                initial_value = current_value.value
                # Attempt to convert from fixed format string to date/time data type
                if 'datetime' == current_value.field.data_type:
                    initial_value = datetime.strptime(initial_value, CUSTOMFIELD_DATETIME_FORMAT)
                elif 'date' == current_value.field.data_type:
                    initial_value = datetime.strptime(initial_value, CUSTOMFIELD_DATE_FORMAT)
                elif 'time' == current_value.field.data_type:
                    initial_value = datetime.strptime(initial_value, CUSTOMFIELD_TIME_FORMAT)
                # If it is boolean field, transform the value to a real boolean instead of a string
                elif 'boolean' == current_value.field.data_type:
                    initial_value = 'True' == initial_value
            except (TicketCustomFieldValue.DoesNotExist, ValueError, TypeError):
                # ValueError error if parsing fails, using initial_value = current_value.value
                # TypeError if parsing None type
                pass
            instanceargs = {
                'label': field.label,
                'help_text': field.help_text,
                'required': field.required,
                'initial': initial_value,
            }

            self.customfield_to_field(field, instanceargs)

    def save(self, *args, **kwargs):

        for field, value in self.cleaned_data.items():
            if field.startswith('custom_'):
                field_name = field.replace('custom_', '', 1)
                customfield = CustomField.objects.get(name=field_name)
                try:
                    cfv = TicketCustomFieldValue.objects.get(ticket=self.instance, field=customfield)
                except ObjectDoesNotExist:
                    cfv = TicketCustomFieldValue(ticket=self.instance, field=customfield)

                # Convert date/time data type to known fixed format string.
                if datetime is type(value):
                    cfv.value = value.strftime(CUSTOMFIELD_DATETIME_FORMAT)
                elif date is type(value):
                    cfv.value = value.strftime(CUSTOMFIELD_DATE_FORMAT)
                elif time is type(value):
                    cfv.value = value.strftime(CUSTOMFIELD_TIME_FORMAT)
                else:
                    cfv.value = value
                cfv.save()

        return super(EditTicketForm, self).save(*args, **kwargs)


class EditFollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        exclude = ('date', 'user',)

    def __init__(self, *args, **kwargs):
        """Filter not openned tickets here."""
        super(EditFollowUpForm, self).__init__(*args, **kwargs)
        self.fields["ticket"].queryset = Ticket.objects.filter(status__in=(Ticket.OPEN_STATUS, Ticket.REOPENED_STATUS))


class AbstractTicketForm(CustomFieldMixin, forms.Form):
    """
    Contain all the common code and fields between "TicketForm" and
    "PublicTicketForm". This Form is not intended to be used directly.
    """
    queue = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-control'}),
        label=_('Queue'),
        required=True,
        choices=()
    )

    title = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Summary of the problem'),
    )

    body = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control'}),
        label=_('Description of your issue'),
        required=True,
        help_text=_('Please be as descriptive as possible and include all details'),
    )

    priority = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-control'}),
        choices=Ticket.PRIORITY_CHOICES,
        required=True,
        initial=getattr(settings, 'HELPDESK_PUBLIC_TICKET_PRIORITY', '3'),
        label=_('Priority'),
        help_text=_("Please select a priority carefully. If unsure, leave it as '3'."),
    )

    due_date = forms.DateTimeField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
        required=False,
        input_formats=[CUSTOMFIELD_DATE_FORMAT, CUSTOMFIELD_DATETIME_FORMAT, '%d/%m/%Y', '%m/%d/%Y', "%d.%m.%Y"],
        label=_('Due on'),
    )

    attachment = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control-file'}),
        required=False,
        label=_('Attach File'),
        help_text=_('You can attach a file such as a document or screenshot to this ticket.'),
    )

    class Media:
        js = ('helpdesk/js/init_due_date.js', 'helpdesk/js/init_datetime_classes.js')

    def __init__(self, kbcategory=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if kbcategory:
            self.fields['kbitem'] = forms.ChoiceField(
                widget=forms.Select(attrs={'class': 'form-control'}),
                required=False,
                label=_('Knowledge Base Item'),
                choices=[(kbi.pk, kbi.title) for kbi in KBItem.objects.filter(category=kbcategory.pk, enabled=True)],
            )

    def _add_form_custom_fields(self, staff_only_filter=None):
        if staff_only_filter is None:
            queryset = CustomField.objects.all()
        else:
            queryset = CustomField.objects.filter(staff_only=staff_only_filter)

        for field in queryset:
            instanceargs = {
                'label': field.label,
                'help_text': field.help_text,
                'required': field.required,
            }

            self.customfield_to_field(field, instanceargs)

    def _get_queue(self):
        # this procedure is re-defined for public submission form
        return Queue.objects.get(id=int(self.cleaned_data['queue']))

    def _create_ticket(self):
        queue = self._get_queue()
        kbitem = None
        if 'kbitem' in self.cleaned_data:
            kbitem = KBItem.objects.get(id=int(self.cleaned_data['kbitem']))

        ticket = Ticket(
            title=self.cleaned_data['title'],
            submitter_email=self.cleaned_data['submitter_email'],
            created=timezone.now(),
            status=Ticket.NEW_STATUS,
            queue=queue,
            description=self.cleaned_data['body'],
            priority=self.cleaned_data.get(
                'priority',
                getattr(settings, "HELPDESK_PUBLIC_TICKET_PRIORITY", "3")
            ),
            due_date=self.cleaned_data.get(
                'due_date',
                getattr(settings, "HELPDESK_PUBLIC_TICKET_DUE_DATE", None)
            ) or None,
            kbitem=kbitem,
        )

        return ticket, queue

    def _create_custom_fields(self, ticket):
        for field, value in self.cleaned_data.items():
            if field.startswith('custom_'):
                field_name = field.replace('custom_', '', 1)
                custom_field = CustomField.objects.get(name=field_name)
                cfv = TicketCustomFieldValue(ticket=ticket,
                                             field=custom_field,
                                             value=value)
                cfv.save()

    def _create_follow_up(self, ticket, title, user=None):
        followup = FollowUp(ticket=ticket,
                            title=title,
                            date=timezone.now(),
                            public=True,
                            comment=self.cleaned_data['body'],
                            )
        if user:
            followup.user = user
        return followup

    def _attach_files_to_follow_up(self, followup):
        files = self.cleaned_data['attachment']
        if files:
            files = process_attachments(followup, [files])
        return files

    @staticmethod
    def _send_messages(ticket, queue, followup, files, user=None):
        context = safe_template_context(ticket)
        context['comment'] = followup.comment

        roles = {'submitter': ('newticket_submitter', context),
                 'new_ticket_cc': ('newticket_cc', context),
                 'ticket_cc': ('newticket_cc', context)}
        if ticket.assigned_to and ticket.assigned_to.usersettings_helpdesk.email_on_ticket_assign:
            roles['assigned_to'] = ('assigned_owner', context)
        ticket.send(
            roles,
            fail_silently=True,
            files=files,
        )


class TicketForm(AbstractTicketForm):
    """
    Ticket Form creation for registered users.
    """
    submitter_email = forms.EmailField(
        required=False,
        label=_('Submitter E-Mail Address'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'type': 'email'}),
        help_text=_('This e-mail address will receive copies of all public '
                    'updates to this ticket.'),
    )
    assigned_to = forms.ChoiceField(
        widget=(
            forms.Select(attrs={'class': 'form-control'})
            if not helpdesk_settings.HELPDESK_CREATE_TICKET_HIDE_ASSIGNED_TO
            else forms.HiddenInput()
        ),
        required=False,
        label=_('Case owner'),
        help_text=_('If you select an owner other than yourself, they\'ll be '
                    'e-mailed details of this ticket immediately.'),

        choices=()
    )

    def __init__(self, *args, **kwargs):
        """
        Add any custom fields that are defined to the form.
        """
        queue_choices = kwargs.pop("queue_choices")

        super().__init__(*args, **kwargs)

        self.fields['queue'].choices = queue_choices
        if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_OWNERS:
            assignable_users = User.objects.filter(is_active=True, is_staff=True).order_by(User.USERNAME_FIELD)
        else:
            assignable_users = User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)
        self.fields['assigned_to'].choices = [('', '--------')] + [(u.id, u.get_username()) for u in assignable_users]
        self._add_form_custom_fields()

    def save(self, user):
        """
        Writes and returns a Ticket() object
        """

        ticket, queue = self._create_ticket()
        if self.cleaned_data['assigned_to']:
            try:
                u = User.objects.get(id=self.cleaned_data['assigned_to'])
                ticket.assigned_to = u
            except User.DoesNotExist:
                ticket.assigned_to = None
        ticket.save()

        self._create_custom_fields(ticket)

        if self.cleaned_data['assigned_to']:
            title = _('Ticket Opened & Assigned to %(name)s') % {
                'name': ticket.get_assigned_to or _("<invalid user>")
            }
        else:
            title = _('Ticket Opened')
        followup = self._create_follow_up(ticket, title=title, user=user)
        followup.save()

        files = self._attach_files_to_follow_up(followup)
        self._send_messages(ticket=ticket,
                            queue=queue,
                            followup=followup,
                            files=files,
                            user=user)
        return ticket


class PublicTicketForm(AbstractTicketForm):
    """
    Ticket Form creation for all users (public-facing).
    """
    submitter_email = forms.EmailField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'type': 'email'}),
        required=True,
        label=_('Your E-Mail Address'),
        help_text=_('We will e-mail you when your ticket is updated.'),
    )

    def __init__(self, hidden_fields=(), readonly_fields=(), *args, **kwargs):
        """
        Add any (non-staff) custom fields that are defined to the form
        """
        super(PublicTicketForm, self).__init__(*args, **kwargs)
        self._add_form_custom_fields(False)

        field_hide_table = {
            'queue': 'HELPDESK_PUBLIC_TICKET_QUEUE',
            'priority': 'HELPDESK_PUBLIC_TICKET_PRIORITY',
            'due_date': 'HELPDESK_PUBLIC_TICKET_DUE_DATE',
        }

        for field_name, field_setting_key in field_hide_table.items():
            has_settings_default_value = getattr(settings, field_setting_key, None)
            if has_settings_default_value is not None:
                hidden_fields += (field_name,)

        for field in hidden_fields:
            if field in self.fields:
                del self.fields[field]

        public_queues = Queue.objects.filter(allow_public_submission=True)

        if len(public_queues) == 0:
            logger.warning(
                "There are no public queues defined - public ticket creation is impossible"
            )

        if 'queue' in self.fields:
            self.fields['queue'].choices = [('', '--------')] + [
                (q.id, q.title) for q in public_queues]

    def _get_queue(self):
        if getattr(settings, 'HELPDESK_PUBLIC_TICKET_QUEUE', None) is not None:
            # force queue to be the pre-defined one
            # (only for public submissions)
            public_queue = Queue.objects.filter(
                slug=settings.HELPDESK_PUBLIC_TICKET_QUEUE
            ).first()
            if not public_queue:
                logger.fatal(
                    "Public queue '%s' is configured as default but can't be found",
                    settings.HELPDESK_PUBLIC_TICKET_QUEUE
                )
            return public_queue
        else:
            # get the queue user entered
            return Queue.objects.get(id=int(self.cleaned_data['queue']))

    def save(self, user):
        """
        Writes and returns a Ticket() object
        """
        ticket, queue = self._create_ticket()
        if queue.default_owner and not ticket.assigned_to:
            ticket.assigned_to = queue.default_owner
        ticket.save()

        self._create_custom_fields(ticket)

        followup = self._create_follow_up(
            ticket, title=_('Ticket Opened Via Web'), user=user)
        followup.save()

        files = self._attach_files_to_follow_up(followup)
        self._send_messages(ticket=ticket,
                            queue=queue,
                            followup=followup,
                            files=files)
        return ticket


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        exclude = ['user', 'settings_pickled']


class EmailIgnoreForm(forms.ModelForm):
    class Meta:
        model = IgnoreEmail
        exclude = []


class TicketCCForm(forms.ModelForm):
    ''' Adds either an email address or helpdesk user as a CC on a Ticket. Used for processing POST requests. '''

    class Meta:
        model = TicketCC
        exclude = ('ticket',)

    def __init__(self, *args, **kwargs):
        super(TicketCCForm, self).__init__(*args, **kwargs)
        if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_CC:
            users = User.objects.filter(is_active=True, is_staff=True).order_by(User.USERNAME_FIELD)
        else:
            users = User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)
        self.fields['user'].queryset = users


class TicketCCUserForm(forms.ModelForm):
    ''' Adds a helpdesk user as a CC on a Ticket '''

    def __init__(self, *args, **kwargs):
        super(TicketCCUserForm, self).__init__(*args, **kwargs)
        if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_CC:
            users = User.objects.filter(is_active=True, is_staff=True).order_by(User.USERNAME_FIELD)
        else:
            users = User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)
        self.fields['user'].queryset = users

    class Meta:
        model = TicketCC
        exclude = ('ticket', 'email',)


class TicketCCEmailForm(forms.ModelForm):
    ''' Adds an email address as a CC on a Ticket '''

    def __init__(self, *args, **kwargs):
        super(TicketCCEmailForm, self).__init__(*args, **kwargs)

    class Meta:
        model = TicketCC
        exclude = ('ticket', 'user',)


class TicketDependencyForm(forms.ModelForm):
    ''' Adds a different ticket as a dependency for this Ticket '''

    class Meta:
        model = TicketDependency
        exclude = ('ticket',)


class MultipleTicketSelectForm(forms.Form):
    tickets = forms.ModelMultipleChoiceField(
        label=_('Tickets to merge'),
        queryset=Ticket.objects.filter(merged_to=None),
        widget=forms.SelectMultiple(attrs={'class': 'form-control'})
    )

    def clean_tickets(self):
        tickets = self.cleaned_data.get('tickets')
        if len(tickets) < 2:
            raise ValidationError(_('Please choose at least 2 tickets.'))
        if len(tickets) > 4:
            raise ValidationError(_('Impossible to merge more than 4 tickets...'))
        queues = tickets.order_by('queue').distinct().values_list('queue', flat=True)
        if len(queues) != 1:
            raise ValidationError(_('All selected tickets must share the same queue in order to be merged.'))
        return tickets
