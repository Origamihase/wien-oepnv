"""KI-Wächter (``scripts/ki_waechter.py``): the parts that need no model.

``inputs`` builds the model's input from the feed versions and their source
events, ``audit`` checks the model's answer and writes the audit file,
``dispatch`` starts the daily audit from the Update cycle when it is due. The
command line and the Gemini client stay in the script. Details in
``docs/reference/ki_waechter.md``.
"""
