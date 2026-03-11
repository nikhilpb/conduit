import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:think_client/conduit_api.dart';
import 'package:think_client/context_estimate.dart';
import 'package:think_client/main.dart';
import 'package:think_client/models.dart';
import 'package:think_client/settings_store.dart';

void main() {
  testWidgets('composer shows estimated context and updates while typing', (
    WidgetTester tester,
  ) async {
    SharedPreferences.setMockInitialValues({
      'location': 'Zurich, Switzerland',
      'personal_instructions': 'Keep it short.',
    });
    final fixedNow = DateTime(2026, 3, 11, 10, 15, 20);
    final socketHarness = _FakeSocketHarness();
    final client = _FakeConduitApiClient(
      healthStatus: const HealthStatus(
        ok: true,
        appName: 'Conduit',
        model: 'claude-sonnet-4-6',
        modelLabel: 'Claude Sonnet 4.6',
        provider: 'anthropic',
        contextCharsPerToken: 4.0,
      ),
      sessionDetail: SessionDetail(
        sessionId: 'session-1',
        messages: const [],
        contextEstimate: _contextEstimate(400),
      ),
      socket: socketHarness.socket,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: ChatScreen(
          client: client,
          settingsStore: SettingsStore(),
          sessionId: 'session-1',
          nowProvider: () => fixedNow,
        ),
      ),
    );
    await tester.pumpAndSettle();

    final hiddenChars = estimateHiddenContextChars(
      currentTime: formatCurrentTimeForContext(fixedNow),
      location: 'Zurich, Switzerland',
      personalInstructions: 'Keep it short.',
    );
    final baseTokens = estimateTokensFromChars(400 + hiddenChars);

    expect(find.text('Est. context'), findsOneWidget);
    expect(find.text(formatTokenEstimate(baseTokens)), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'Hello world');
    await tester.pump();

    final updatedTokens = estimateTokensFromChars(400 + hiddenChars + 11);
    expect(find.text(formatTokenEstimate(updatedTokens)), findsOneWidget);
  });

  testWidgets(
    'composer tracks pending tool/result growth and reconciles on done',
    (WidgetTester tester) async {
      SharedPreferences.setMockInitialValues({});
      final fixedNow = DateTime(2026, 3, 11, 10, 15, 20);
      final socketHarness = _FakeSocketHarness();
      final client = _FakeConduitApiClient(
        healthStatus: const HealthStatus(
          ok: true,
          appName: 'Conduit',
          model: 'claude-sonnet-4-6',
          modelLabel: 'Claude Sonnet 4.6',
          provider: 'anthropic',
          contextCharsPerToken: 4.0,
        ),
        sessionDetail: SessionDetail(
          sessionId: 'session-1',
          messages: const [],
          contextEstimate: _contextEstimate(200),
        ),
        socket: socketHarness.socket,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: ChatScreen(
            client: client,
            settingsStore: SettingsStore(),
            sessionId: 'session-1',
            nowProvider: () => fixedNow,
          ),
        ),
      );
      await tester.pumpAndSettle();

      final hiddenChars = estimateHiddenContextChars(
        currentTime: formatCurrentTimeForContext(fixedNow),
        location: '',
        personalInstructions: '',
      );
      const draft = 'Look up Zurich weather';

      await tester.enterText(find.byType(TextField).first, draft);
      await tester.pump();

      await tester.tap(find.byIcon(Icons.arrow_upward));
      await tester.pump();

      expect(socketHarness.sentPayloads, hasLength(1));
      expect(socketHarness.sentPayloads.single['content'], draft);

      var expectedChars = 200 + hiddenChars + draft.length;
      expect(
        find.text(formatTokenEstimate(estimateTokensFromChars(expectedChars))),
        findsOneWidget,
      );

      socketHarness.emit({
        'type': 'ack',
        'message_id': socketHarness.sentPayloads.single['message_id'],
        'session_id': 'session-1',
        'turn_id': 'turn-1',
      });
      await tester.pump();

      socketHarness.emit({
        'type': 'tool_call',
        'turn_id': 'turn-1',
        'tool_call_id': 'tc_1',
        'tool': 'web_search',
        'args': {'query': draft},
        'status': 'pending',
      });
      await tester.pump();

      expectedChars += estimateToolCallChars('web_search', {'query': draft});
      expect(
        find.text(formatTokenEstimate(estimateTokensFromChars(expectedChars))),
        findsOneWidget,
      );

      socketHarness.emit({
        'type': 'tool_result',
        'turn_id': 'turn-1',
        'tool_call_id': 'tc_1',
        'tool': 'web_search',
        'status': 'completed',
        'context_chars_delta': 180,
      });
      await tester.pump();

      expectedChars += 180;
      expect(
        find.text(formatTokenEstimate(estimateTokensFromChars(expectedChars))),
        findsOneWidget,
      );

      socketHarness.emit({
        'type': 'token',
        'turn_id': 'turn-1',
        'message_id': 'assistant-1',
        'content': 'Forecast summary.',
      });
      await tester.pump();

      expectedChars += 'Forecast summary.'.length;
      expect(
        find.text(formatTokenEstimate(estimateTokensFromChars(expectedChars))),
        findsOneWidget,
      );

      socketHarness.emit({
        'type': 'done',
        'turn_id': 'turn-1',
        'session_id': 'session-1',
        'message_id': 'assistant-1',
        'context_estimate': _contextEstimate(1234).toJson(),
      });
      await tester.pump();

      final reconciledTokens = estimateTokensFromChars(1234 + hiddenChars);
      expect(find.text(formatTokenEstimate(reconciledTokens)), findsOneWidget);
    },
  );
}

class _FakeConduitApiClient extends ConduitApiClient {
  _FakeConduitApiClient({
    required this.healthStatus,
    required this.sessionDetail,
    required this.socket,
  }) : super(baseUrl: 'http://example.com');

  final HealthStatus healthStatus;
  final SessionDetail sessionDetail;
  final ConduitChatSocket socket;

  @override
  Future<HealthStatus> health() async => healthStatus;

  @override
  Future<SessionDetail> getSession(String sessionId) async => sessionDetail;

  @override
  ConduitChatSocket createChatSocket() => socket;
}

class _FakeSocketHarness {
  final controller = StreamController<ChatServerEvent>.broadcast();
  final List<Map<String, dynamic>> sentPayloads = [];

  late final ConduitChatSocket socket = ConduitChatSocket.test(
    events: controller.stream,
    onSend: (payload) async {
      sentPayloads.add(Map<String, dynamic>.from(payload));
    },
    onDispose: () async {
      if (!controller.isClosed) {
        await controller.close();
      }
    },
  );

  void emit(Map<String, dynamic> payload) {
    controller.add(ChatServerEvent.fromJson(payload));
  }
}

ContextEstimate _contextEstimate(int chars) {
  return ContextEstimate(
    chars: chars,
    tokens: estimateTokensFromChars(chars),
    charsPerToken: defaultContextCharsPerToken,
  );
}

extension on ContextEstimate {
  Map<String, dynamic> toJson() => {
    'chars': chars,
    'tokens': tokens,
    'chars_per_token': charsPerToken,
  };
}
