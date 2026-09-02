class EmergencyContact {
  final String name;
  final String msisdn;

  const EmergencyContact({required this.name, required this.msisdn});

  Map<String, dynamic> toJson() => {'name': name, 'msisdn': msisdn};

  factory EmergencyContact.fromJson(Map<String, dynamic> json) =>
      EmergencyContact(
        name: json['name'] as String,
        msisdn: json['msisdn'] as String,
      );
}
