"""Multi-step missions via PlanSys2.

Only used when an IntentRequest contains multiple plant-targeted intents
that benefit from ordered planning (e.g. visit-and-water tomatoes,
lettuce, and herbs). Single-intent requests bypass this and go straight
to the py_trees builder.

Requires the PlanSys2 ROS2 packages (``ros-humble-plansys2-*``) and a
running PlanSys2 bringup. See ``plansys2_controller.py`` for details.
"""
