import 'package:shared_preferences/shared_preferences.dart';

class UserContextSettings {
  const UserContextSettings({
    required this.location,
    required this.personalInstructions,
  });

  final String location;
  final String personalInstructions;
}

class SettingsStore {
  static const _serverUrlKey = 'server_url';
  static const _locationKey = 'location';
  static const _personalInstructionsKey = 'personal_instructions';

  Future<String?> loadServerUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final value = prefs.getString(_serverUrlKey)?.trim();
    if (value == null || value.isEmpty) {
      return null;
    }
    return value;
  }

  Future<void> saveServerUrl(String serverUrl) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_serverUrlKey, serverUrl.trim());
  }

  Future<UserContextSettings> loadUserContextSettings() async {
    final prefs = await SharedPreferences.getInstance();
    return UserContextSettings(
      location: prefs.getString(_locationKey)?.trim() ?? '',
      personalInstructions:
          prefs.getString(_personalInstructionsKey)?.trim() ?? '',
    );
  }

  Future<void> saveUserContextSettings({
    required String location,
    required String personalInstructions,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_locationKey, location.trim());
    await prefs.setString(
      _personalInstructionsKey,
      personalInstructions.trim(),
    );
  }
}
