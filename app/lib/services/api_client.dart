import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/contact.dart';
import '../models/trip.dart';
import '../models/zone.dart';
import 'storage_service.dart';

class ApiException implements Exception {
  final String message;
  ApiException(this.message);
  @override
  String toString() => message;
}

/// Talks to the real product surface only — /travellers, /zones,
/// /travellers/me/*. Never touches /demo/* (that's the conductor's job).
/// See backend/app/routers/real.py.
class ApiClient {
  final StorageService storage;
  final http.Client _http;

  ApiClient(this.storage, {http.Client? client})
    : _http = client ?? http.Client();

  Uri _uri(String path) => Uri.parse('${storage.backendUrl}$path');

  Map<String, String> get _authHeaders {
    final token = storage.authToken;
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Future<List<Zone>> fetchZones() async {
    final resp = await _http
        .get(_uri('/zones'), headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException('failed to load zones (${resp.statusCode})');
    }
    final list = jsonDecode(resp.body) as List;
    return list.map((e) => Zone.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<void> registerTraveller({
    required String msisdn,
    required String name,
    required List<EmergencyContact> contacts,
  }) async {
    final resp = await _http
        .post(
          _uri('/travellers'),
          headers: _authHeaders,
          body: jsonEncode({
            'msisdn': msisdn,
            'name': name,
            'contacts': contacts.map((c) => c.toJson()).toList(),
          }),
        )
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      String detail = '';
      try {
        detail = (jsonDecode(resp.body) as Map<String, dynamic>)['detail'] as String? ?? '';
      } catch (_) {
        // non-JSON error body — fall through with an empty detail
      }
      throw ApiException(
        detail.isNotEmpty ? detail : 'registration failed (${resp.statusCode})',
      );
    }
    final body = jsonDecode(resp.body) as Map<String, dynamic>;
    await storage.setTravellerId(body['traveller_id'] as String);
    await storage.setAuthToken(body['auth_token'] as String);
  }

  Future<void> reportBattery(int level) async {
    final uri = _uri('/travellers/me/battery').replace(
      queryParameters: {'level': '$level'},
    );
    try {
      await _http.post(uri, headers: _authHeaders).timeout(
        const Duration(seconds: 8),
      );
    } catch (_) {
      // Battery reporting is best-effort — a dropped report while inside a
      // dead zone is expected, not an error. The agent already read the
      // last-known level before signal was lost.
    }
  }

  /// Returns null when there is no active trip (the common case).
  Future<Trip?> fetchCurrentTrip() async {
    final resp = await _http
        .get(_uri('/travellers/me/trip'), headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException('failed to load trip (${resp.statusCode})');
    }
    if (resp.body.trim() == 'null' || resp.body.trim().isEmpty) return null;
    return Trip.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }
}
