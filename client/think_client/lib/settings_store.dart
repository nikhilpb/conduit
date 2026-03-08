import 'package:shared_preferences/shared_preferences.dart';

class SettingsStore {
  static const _serverUrlKey = 'server_url';

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
}
