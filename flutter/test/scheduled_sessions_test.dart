import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:think_client/conduit_api.dart';
import 'package:think_client/main.dart';
import 'package:think_client/models.dart';
import 'package:think_client/settings_store.dart';

void main() {
  testWidgets('scheduled sessions show a badge in the session list', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SessionCard(
            session: const SessionSummary(
              sessionId: 'scheduled-1',
              lastUpdateTime: 0,
              eventCount: 2,
              title: 'Daily brief',
              kind: 'scheduled',
              readOnly: true,
            ),
            onTap: () {},
          ),
        ),
      ),
    );

    expect(find.text('Scheduled'), findsOneWidget);
  });

  testWidgets('scheduled chat sessions disable the composer', (
    WidgetTester tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final client = ConduitApiClient(
      baseUrl: 'http://example.com',
      httpClient: MockClient(_mockScheduledSessionResponse),
      chatSocketFactory: () =>
          ConduitChatSocket.test(events: const Stream.empty()),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: ChatScreen(
          client: client,
          settingsStore: SettingsStore(),
          sessionId: 'scheduled-1',
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('generated automatically'), findsOneWidget);
    expect(find.byIcon(Icons.schedule_rounded), findsOneWidget);

    final textField = tester.widget<TextField>(find.byType(TextField).first);
    expect(textField.enabled, isFalse);

    final sendButton = tester.widget<FilledButton>(find.byType(FilledButton));
    expect(sendButton.onPressed, isNull);
  });
}

Future<http.Response> _mockScheduledSessionResponse(
  http.Request request,
) async {
  if (request.url.path == '/health') {
    return http.Response(
      jsonEncode({
        'ok': true,
        'app_name': 'Conduit',
        'model': 'claude-sonnet-4-6',
        'model_label': 'Claude Sonnet 4.6',
        'provider': 'anthropic',
        'context_chars_per_token': 4.0,
      }),
      200,
    );
  }

  if (request.url.path == '/sessions/scheduled-1') {
    return http.Response(
      jsonEncode({
        'session_id': 'scheduled-1',
        'kind': 'scheduled',
        'read_only': true,
        'messages': [
          {
            'message_id': 'user-1',
            'role': 'user',
            'text': 'What changed overnight?',
            'created_at': 1,
            'thinking_trace': '',
            'tool_calls': const [],
          },
          {
            'message_id': 'assistant-1',
            'role': 'assistant',
            'text': 'Automated answer.',
            'created_at': 2,
            'thinking_trace': '',
            'tool_calls': const [],
          },
        ],
        'context_estimate': {'chars': 42, 'tokens': 11, 'chars_per_token': 4.0},
      }),
      200,
    );
  }

  throw UnsupportedError('Unhandled request: ${request.url}');
}
