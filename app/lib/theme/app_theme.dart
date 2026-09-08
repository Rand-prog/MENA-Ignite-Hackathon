import 'package:flutter/material.dart';

/// SignalGuard's palette.
///
/// Dark-first, because the app is used in a car and often at night. Green
/// reads as "safe / silent" (the happy path — see docs/SignalGuard_User_
/// Flow), amber as "preparing", red only for the overdue states.
///
/// But dark-only was wrong, and wrong in the one place this app is
/// guaranteed to be used: a phone in a windshield mount on a desert
/// highway at midday sits under something like 100,000 lux, where a
/// #0B0F0E background with #9DB0AB secondary text is simply not readable.
/// The offline map screen — the one that matters when everything else has
/// failed — was the worst affected. So there are two palettes, and
/// [AppPalette] is the indirection that lets every screen read the right
/// one without a `if (isDark)` at each call site.
///
/// The daylight palette is not a tint-inverted copy: it raises contrast
/// well past the dark theme's (text on background is ~13:1, versus the
/// ~11:1 of the dark one) and drops the accent's luminance so green on
/// white still reads as green rather than as a highlighter.
class AppColors {
  // -- night (default) -------------------------------------------------
  static const bg = Color(0xFF0B0F0E);
  static const surface = Color(0xFF141B19);
  static const surfaceRaised = Color(0xFF1C2624);
  static const border = Color(0xFF283330);
  static const textPrimary = Color(0xFFEAF2EF);
  static const textSecondary = Color(0xFF9DB0AB);
  static const textMuted = Color(0xFF64756F);
  static const accent = Color(0xFF2FD98A); // "safe" green
  static const accentDim = Color(0xFF1B7A50);
  static const amber = Color(0xFFE6B94D); // "preparing" state
  static const danger = Color(0xFFE0654F);

  // -- daylight ---------------------------------------------------------
  static const dayBg = Color(0xFFF7FAF8);
  static const daySurface = Color(0xFFFFFFFF);
  static const daySurfaceRaised = Color(0xFFECF2EF);
  static const dayBorder = Color(0xFFC5D2CD);
  static const dayTextPrimary = Color(0xFF0B1512);
  static const dayTextSecondary = Color(0xFF3D4E49);
  static const dayTextMuted = Color(0xFF5C6E68);
  static const dayAccent = Color(0xFF0C7A47);
  static const dayAccentDim = Color(0xFF0A5F37);
  static const dayAmber = Color(0xFF8A5A00);
  static const dayDanger = Color(0xFFB3311A);
}

/// The colours a screen should actually use, resolved for the active
/// brightness. Read it as `AppPalette.of(context)`.
class AppPalette {
  final bool isDay;
  const AppPalette._(this.isDay);

  static AppPalette of(BuildContext context) =>
      AppPalette._(Theme.of(context).brightness == Brightness.light);

  Color get bg => isDay ? AppColors.dayBg : AppColors.bg;
  Color get surface => isDay ? AppColors.daySurface : AppColors.surface;
  Color get surfaceRaised =>
      isDay ? AppColors.daySurfaceRaised : AppColors.surfaceRaised;
  Color get border => isDay ? AppColors.dayBorder : AppColors.border;
  Color get textPrimary =>
      isDay ? AppColors.dayTextPrimary : AppColors.textPrimary;
  Color get textSecondary =>
      isDay ? AppColors.dayTextSecondary : AppColors.textSecondary;
  Color get textMuted => isDay ? AppColors.dayTextMuted : AppColors.textMuted;
  Color get accent => isDay ? AppColors.dayAccent : AppColors.accent;
  Color get accentDim => isDay ? AppColors.dayAccentDim : AppColors.accentDim;
  Color get amber => isDay ? AppColors.dayAmber : AppColors.amber;
  Color get danger => isDay ? AppColors.dayDanger : AppColors.danger;
  Color get onAccent =>
      isDay ? const Color(0xFFFFFFFF) : const Color(0xFF04150E);
}

ThemeData buildAppTheme({Brightness brightness = Brightness.dark}) {
  final isDay = brightness == Brightness.light;
  final base = isDay ? ThemeData.light(useMaterial3: true)
                     : ThemeData.dark(useMaterial3: true);

  final bg = isDay ? AppColors.dayBg : AppColors.bg;
  final surface = isDay ? AppColors.daySurface : AppColors.surface;
  final border = isDay ? AppColors.dayBorder : AppColors.border;
  final textPrimary = isDay ? AppColors.dayTextPrimary : AppColors.textPrimary;
  final textSecondary =
      isDay ? AppColors.dayTextSecondary : AppColors.textSecondary;
  final textMuted = isDay ? AppColors.dayTextMuted : AppColors.textMuted;
  final accent = isDay ? AppColors.dayAccent : AppColors.accent;
  final danger = isDay ? AppColors.dayDanger : AppColors.danger;
  final onAccent = isDay ? const Color(0xFFFFFFFF) : const Color(0xFF04150E);

  return base.copyWith(
    scaffoldBackgroundColor: bg,
    colorScheme: base.colorScheme.copyWith(
      brightness: brightness,
      primary: accent,
      onPrimary: onAccent,
      surface: surface,
      onSurface: textPrimary,
      error: danger,
    ),
    textTheme: base.textTheme
        .apply(bodyColor: textPrimary, displayColor: textPrimary)
        .copyWith(
          // Each of these replaces the whole TextStyle object, which
          // silently drops the color .apply() just set above — a TextStyle
          // with no color falls back to DefaultTextStyle's, which reads as
          // near-black-on-black here. Every screen's main heading uses
          // headlineMedium, so this was a real, high-impact contrast bug,
          // not a style nit — caught by actually running the app rather
          // than reading the theme file in isolation.
          headlineMedium: TextStyle(
            fontSize: 26,
            fontWeight: FontWeight.w600,
            letterSpacing: -0.3,
            height: 1.2,
            color: textPrimary,
          ),
          titleLarge: TextStyle(
            fontSize: 19,
            fontWeight: FontWeight.w600,
            letterSpacing: -0.1,
            color: textPrimary,
          ),
          bodyLarge: TextStyle(fontSize: 16, height: 1.45, color: textPrimary),
          bodyMedium: TextStyle(fontSize: 14, height: 1.4, color: textSecondary),
          labelLarge: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.2,
            color: textPrimary,
          ),
        ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: accent,
        foregroundColor: onAccent,
        minimumSize: const Size.fromHeight(52),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        elevation: 0,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: textPrimary,
        minimumSize: const Size.fromHeight(52),
        side: BorderSide(color: border),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: textSecondary),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: accent, width: 1.5),
      ),
      hintStyle: TextStyle(color: textMuted),
    ),
    dividerTheme: DividerThemeData(color: border, thickness: 1),
  );
}
