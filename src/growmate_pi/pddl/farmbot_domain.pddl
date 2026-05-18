; GrowMate Pi - PDDL domain for multi-step garden missions.
;
; Used by the PlanSys2 mission controller (mission/plansys2_controller.py)
; to plan ordered visits to plants when the client emits multiple intents.
;
; Modelled on the EE650 energy_domain pattern:
;   - "active" predicates guard which goal-tier actions can run
;   - "_cleared" predicates are flipped by completing a tier and act as
;     preconditions for lower-tier actions
;   - POPF doesn't reliably support :negative-preconditions; we use
;     positive cleared-flags instead of `not active`.
;
; Three priority tiers, matching the FarmBot care lifecycle:
;
;   critical : plants flagged as wilting/dry (always handled first)
;   high     : routine watering on schedule
;   normal   : sensor checks, photos
;
; The robot is always "at" exactly one plant. Movement actions transition
; the robot between plants.

(define (domain farmbot_garden)

  (:requirements :strips :typing)

  (:types
    robot
    plant
  )

  (:predicates
    (robot_at ?r - robot ?p - plant)
    (visited  ?p - plant)
    (watered  ?p - plant)
    (sensed   ?p - plant)
    (photographed ?p - plant)

    ; critical-tier (wilting / dry plants)
    (critical_active)
    (critical_at ?p - plant)
    (critical_cleared)

    ; high-tier (routine watering)
    (high_active)
    (high_at ?p - plant)
    (high_cleared)

    ; normal-tier marker (sensors, photos, anything that isn't an emergency)
    (is_normal_plant ?p - plant)

    ; safety / robot state
    (robot_available)            ; bridge ready, no e-stop active
    (in_bounds ?p - plant)       ; planner only considers plants in workspace
  )

  ; -- Movement -------------------------------------------------------------

  (:action move
    :parameters (?r - robot ?from - plant ?to - plant)
    :precondition (and
      (robot_at ?r ?from)
      (in_bounds ?to)
      (robot_available)
    )
    :effect (and
      (robot_at ?r ?to)
      (not (robot_at ?r ?from))
    )
  )

  ; -- Critical-tier action ------------------------------------------------

  (:action water_critical
    :parameters (?r - robot ?p - plant)
    :precondition (and
      (robot_at ?r ?p)
      (critical_active)
      (critical_at ?p)
      (in_bounds ?p)
      (robot_available)
    )
    :effect (and
      (visited ?p)
      (watered ?p)
      (critical_cleared)
      (not (critical_active))
    )
  )

  ; -- High-tier action ----------------------------------------------------

  (:action water_high
    :parameters (?r - robot ?p - plant)
    :precondition (and
      (robot_at ?r ?p)
      (high_active)
      (high_at ?p)
      (critical_cleared)
      (in_bounds ?p)
      (robot_available)
    )
    :effect (and
      (visited ?p)
      (watered ?p)
      (high_cleared)
      (not (high_active))
    )
  )

  ; -- Normal-tier actions -------------------------------------------------

  (:action water_normal
    :parameters (?r - robot ?p - plant)
    :precondition (and
      (robot_at ?r ?p)
      (is_normal_plant ?p)
      (critical_cleared)
      (high_cleared)
      (in_bounds ?p)
      (robot_available)
    )
    :effect (and
      (visited ?p)
      (watered ?p)
    )
  )

  (:action check_sensor_at
    :parameters (?r - robot ?p - plant)
    :precondition (and
      (robot_at ?r ?p)
      (is_normal_plant ?p)
      (critical_cleared)
      (high_cleared)
      (in_bounds ?p)
      (robot_available)
    )
    :effect (and
      (visited ?p)
      (sensed ?p)
    )
  )

  (:action photo_at
    :parameters (?r - robot ?p - plant)
    :precondition (and
      (robot_at ?r ?p)
      (is_normal_plant ?p)
      (critical_cleared)
      (high_cleared)
      (in_bounds ?p)
      (robot_available)
    )
    :effect (and
      (visited ?p)
      (photographed ?p)
    )
  )

)
