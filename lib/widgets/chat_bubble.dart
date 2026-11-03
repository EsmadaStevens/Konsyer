import 'package:flutter/material.dart';
import 'package:konsyer/cards/contact_card.dart';
import 'package:konsyer/cards/plan_card.dart';
import 'package:konsyer/core/theme.dart';
import 'package:konsyer/models/chat_message.dart';

class ChatBubble extends StatelessWidget {
  final Msg m;
  const ChatBubble(this.m);
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Align(
      alignment: m.fromUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.9),
        child: Column(
          crossAxisAlignment: m.fromUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            Container(
              margin: const EdgeInsets.symmetric(vertical: 4),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: m.fromUser ? cs.primaryContainer : K.surface,
                border: m.fromUser ? null : Border.all(color: K.border),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(16),
                  topRight: const Radius.circular(16),
                  bottomLeft: Radius.circular(m.fromUser ? 16 : 4),
                  bottomRight: Radius.circular(m.fromUser ? 4 : 16),
                ),
              ),
              child: SelectableText(m.text, style: const TextStyle(height: 1.4)),
            ),
            for (final c in m.cards)
              if (c['type'] == 'plan')
                PlanCard(c)
              else if (c['type'] == 'contact')
                ContactCard(c),
          ],
        ),
      ),
    );
  }
}
