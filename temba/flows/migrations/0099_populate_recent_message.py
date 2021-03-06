# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-05-26 08:33
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import migrations


def get_recent_messages_for_segment(FlowPathRecentStep, from_uuid, to_uuid):
    recent_steps = FlowPathRecentStep.objects.filter(from_uuid=from_uuid, to_uuid=to_uuid)
    recent_steps = recent_steps.prefetch_related('step__messages').order_by('-left_on')

    messages = []
    for r in recent_steps:
        for msg in r.step.messages.all():
            if msg.visibility == 'V':
                msg.run_id = r.step.run_id
                messages.append(msg)

                if len(messages) >= 5:
                    return messages
    return messages


def populate_recent_message(FlowPathRecentStep, FlowPathRecentMessage):
    # get the unique flow path segments
    segments = list(FlowPathRecentStep.objects.values_list('from_uuid', 'to_uuid').distinct('from_uuid', 'to_uuid'))

    for s, segment in enumerate(segments):
        from_uuid = segment[0]
        to_uuid = segment[1]
        messages = get_recent_messages_for_segment(FlowPathRecentStep, from_uuid, to_uuid)

        recent_messages = []
        for msg in messages:
            r = FlowPathRecentMessage(from_uuid=from_uuid, to_uuid=to_uuid,
                                      run_id=msg.run_id, text=msg.text[:640], created_on=msg.created_on)
            recent_messages.append(r)

        FlowPathRecentMessage.objects.bulk_create(recent_messages)

        if (s + 1) % 100 == 0:
            print("Converted recent steps to recent messages for %d of %d segments" % (s + 1, len(segments)))


def apply_manual():
    from temba.flows.models import FlowPathRecentStep, FlowPathRecentMessage
    populate_recent_message(FlowPathRecentStep, FlowPathRecentMessage)


def apply_as_migration(apps, schema_editor):
    FlowPathRecentStep = apps.get_model('flows', 'FlowPathRecentStep')
    FlowPathRecentMessage = apps.get_model('flows', 'FlowPathRecentMessage')
    populate_recent_message(FlowPathRecentStep, FlowPathRecentMessage)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0098_flowpathrecentmessage'),
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
