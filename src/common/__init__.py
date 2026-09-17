"""Code shared by both implementations.

Nothing in here knows about the Microsoft Agent Framework or the Foundry Agent
Service. It is the part of the sample that would be the same whatever runtime
you picked: reading `.env`, checking the database is reachable, the system
prompt describing the schema, and printing the SQL the model wrote.
"""
