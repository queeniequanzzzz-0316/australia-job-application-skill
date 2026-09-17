import argparse
import csv
import pathlib
import shutil


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPOSITORY_ROOT / "assets" / "workspace-template"

WORKSPACE_DIRECTORIES = (
    "profile",
    "filters",
    "jobs",
    "jobs/snapshots",
    "resumes",
    "cover-letters",
    "tracker",
    "reports",
    "dashboard",
)

WORKSPACE_FILES = (
    "profile/candidate-profile.yaml",
    "profile/analytics-consent.yaml",
    "filters/search-filter.yaml",
    "tracker/applications.csv",
    "tracker/events.jsonl",
)

TEMPLATE_FILES = (
    "candidate-profile.example.yaml",
    "search-filter.example.yaml",
    "applications.example.csv",
    "analytics-consent.example.yaml",
)

APPLICATION_HEADER = (
    "canonical_job_id",
    "jd_snapshot_path",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "discovery_status",
    "verification_status",
    "screening_status",
    "application_stage",
    "action_status",
    "first_seen_at",
    "last_verified_at",
    "applied_at",
    "interview_at",
    "offer_at",
    "first_response_at",
    "next_action",
    "action_due_at",
    "blocker_category",
)


class UnsafeWorkspacePathError(ValueError):
    pass


def parse_arguments():
    parser = argparse.ArgumentParser(description="Initialize a private job search workspace.")
    parser.add_argument(
        "--destination",
        required=True,
        type=pathlib.Path,
        help="Path to the private workspace to create or update.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Restore candidate-profile.yaml from its public example template.",
    )
    return parser.parse_args(), parser


def validate_private_destination(destination):
    try:
        expanded_destination = destination.expanduser()
        destination_parent = expanded_destination.parent
        if (
            not destination_parent.exists()
            or not destination_parent.is_dir()
            or destination_parent.is_symlink()
            or expanded_destination.is_symlink()
            or (
                expanded_destination.exists()
                and not expanded_destination.is_dir()
            )
        ):
            raise UnsafeWorkspacePathError

        resolved_destination = expanded_destination.resolve()
        resolved_repository = REPOSITORY_ROOT.resolve()
        if (
            resolved_destination == resolved_repository
            or resolved_repository in resolved_destination.parents
        ):
            raise UnsafeWorkspacePathError
    except UnsafeWorkspacePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeWorkspacePathError from error
    return resolved_destination


def validate_contained_paths(root, paths):
    try:
        resolved_root = root.resolve()
        for path in paths:
            relative_path = path.relative_to(root)
            current_path = root
            for part in relative_path.parts:
                current_path /= part
                if current_path.is_symlink():
                    raise UnsafeWorkspacePathError

            resolved_path = path.resolve()
            if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
                raise UnsafeWorkspacePathError
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, UnsafeWorkspacePathError):
            raise
        raise UnsafeWorkspacePathError from error


def validate_mutation_plan(destination):
    template_paths = [TEMPLATE_ROOT / name for name in TEMPLATE_FILES]
    validate_contained_paths(REPOSITORY_ROOT, [TEMPLATE_ROOT, *template_paths])

    workspace_directory_paths = [
        destination / path for path in WORKSPACE_DIRECTORIES
    ]
    workspace_file_paths = [destination / path for path in WORKSPACE_FILES]
    workspace_paths = [*workspace_directory_paths, *workspace_file_paths]
    validate_contained_paths(destination, workspace_paths)

    try:
        for path in workspace_directory_paths:
            if path.exists() and not path.is_dir():
                raise UnsafeWorkspacePathError

        for path in workspace_file_paths:
            if path.exists() and (
                not path.is_file() or path.stat().st_nlink > 1
            ):
                raise UnsafeWorkspacePathError
    except UnsafeWorkspacePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeWorkspacePathError from error


def validate_applications_template():
    template_path = TEMPLATE_ROOT / "applications.example.csv"
    with template_path.open(newline="", encoding="utf-8") as template_file:
        rows = list(csv.reader(template_file))
    if rows != [list(APPLICATION_HEADER)]:
        raise ValueError("applications.example.csv must contain only the canonical header")


def copy_if_absent(source, destination):
    if not destination.exists():
        shutil.copyfile(source, destination)


def initialise_workspace(destination, force):
    validate_mutation_plan(destination)
    validate_applications_template()

    for relative_directory in WORKSPACE_DIRECTORIES:
        (destination / relative_directory).mkdir(parents=True, exist_ok=True)

    candidate_profile = destination / "profile" / "candidate-profile.yaml"
    if force or not candidate_profile.exists():
        shutil.copyfile(
            TEMPLATE_ROOT / "candidate-profile.example.yaml", candidate_profile
        )

    copy_if_absent(
        TEMPLATE_ROOT / "search-filter.example.yaml",
        destination / "filters" / "search-filter.yaml",
    )
    copy_if_absent(
        TEMPLATE_ROOT / "analytics-consent.example.yaml",
        destination / "profile" / "analytics-consent.yaml",
    )

    applications_path = destination / "tracker" / "applications.csv"
    if not applications_path.exists():
        with applications_path.open("w", newline="", encoding="utf-8") as output_file:
            csv.writer(output_file).writerow(APPLICATION_HEADER)

    (destination / "tracker" / "events.jsonl").touch(exist_ok=True)


def main():
    arguments, parser = parse_arguments()
    try:
        destination = validate_private_destination(arguments.destination)
        initialise_workspace(destination, arguments.force)
    except (UnsafeWorkspacePathError, OSError):
        parser.error("unsafe workspace path")


if __name__ == "__main__":
    main()
