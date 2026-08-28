from uuid import uuid4

from upm_shared.contracts.deployments import (
    EventDeploymentSnapshot,
    ParticipationSnapshot,
    PersonProfile,
    PresentationPresenterSnapshot,
    PresentationSessionSnapshot,
    PresentationSnapshot,
    SessionParticipantSnapshot,
    SessionSnapshot,
)


def test_deployment_preserves_three_row_presentations_in_one_session():
    event_id, site_id, session_id = uuid4(), uuid4(), uuid4()
    people = [uuid4() for _ in range(3)]
    participations = [uuid4() for _ in range(3)]
    presentations = [uuid4() for _ in range(3)]
    snapshot = EventDeploymentSnapshot(
        deployment_id=uuid4(),
        deployment_revision=1,
        event_id=event_id,
        site_id=site_id,
        event_name="Show",
        people=[
            PersonProfile(person_id=person, display_name=f"Presenter {index}", central_revision=1)
            for index, person in enumerate(people)
        ],
        participations=[
            ParticipationSnapshot(
                event_participation_id=participation,
                person_id=person,
                is_presenter=True,
                central_revision=1,
            )
            for participation, person in zip(participations, people, strict=True)
        ],
        sessions=[
            SessionSnapshot(
                session_id=session_id,
                title="Future of AI",
                central_revision=1,
                participants=[
                    SessionParticipantSnapshot(
                        session_participant_id=uuid4(),
                        event_participation_id=participation,
                        role="presenter",
                        presenter_order=index,
                        central_revision=1,
                    )
                    for index, participation in enumerate(participations)
                ],
            )
        ],
        presentations=[
            PresentationSnapshot(
                presentation_id=presentation,
                session_id=session_id,
                title="Future of AI",
                central_revision=1,
                sessions=[
                    PresentationSessionSnapshot(
                        presentation_session_id=uuid4(),
                        session_id=session_id,
                        primary_session=True,
                        central_revision=1,
                    )
                ],
                presenters=[
                    PresentationPresenterSnapshot(
                        presentation_presenter_id=uuid4(),
                        event_participation_id=participation,
                        primary_presenter=True,
                        central_revision=1,
                    )
                ],
            )
            for presentation, participation in zip(presentations, participations, strict=True)
        ],
    )

    assert len(snapshot.sessions) == 1
    assert len(snapshot.presentations) == 3
    assert {item.presentation_id for item in snapshot.presentations} == set(presentations)
    assert [
        item.presenters[0].event_participation_id for item in snapshot.presentations
    ] == participations
