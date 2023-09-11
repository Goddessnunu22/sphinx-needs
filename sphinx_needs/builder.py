from __future__ import annotations

import os
from typing import Any, Iterable, Sequence, Set

from sphinx import version_info
from sphinx.application import Sphinx
from sphinx.builders import Builder

from sphinx_needs.config import NeedsSphinxConfig
from sphinx_needs.data import NeedsInfoType, SphinxNeedsData
from sphinx_needs.logging import get_logger
from sphinx_needs.needsfile import NeedsList

log = get_logger(__name__)


class NeedsBuilderBase(Builder):
    """This is the base class for builders that return a representation of the needs data.

    Subclasses of the builder should specify the ``name`` class attribute,
    and implement the ``finish()`` method,
    to write the needs data in the required format to the output directory.

    The builder normally assumes that the data (stored in the environment)
    is finalised before the write stage.
    This allows for the full write stage to be skipped,
    skipping the unnecessary reading and resolution of cached doctrees,
    which makes the builder a lot more efficient.
    This can be disabled by setting ``skip_write_phase`` to False.
    """

    format = "needs"
    file_suffix = ".txt"
    links_suffix = None
    skip_write_phase: bool = True

    def get_outdated_docs(self) -> Iterable[str]:
        return self.env.found_docs

    def get_target_uri(self, _docname: str, _typ: str | None = None) -> str:
        return ""

    def write(
        self,
        build_docnames: Iterable[str] | None,
        updated_docnames: Sequence[str],
        method: str = "update",
    ) -> None:
        if self.skip_write_phase:
            return
        return super().write(build_docnames, updated_docnames, method)
    
    def prepare_writing(self, _docnames: set[str]) -> None:
        # only needed for subclasses that run the write phase
        pass

    def write_doc(self, _docname: str, _doctree: Any) -> None:
        # only needed for subclasses that run the write phase
        pass

    def write_doc_serialized(self, _docname: str, _doctree: Any) -> None:
        # only needed for subclasses that run the write phase
        pass

    def cleanup(self) -> None:
        # only needed for subclasses that run the write phase
        pass

    @property
    def needs_data(self) -> SphinxNeedsData:
        return SphinxNeedsData(self.env)

    @property
    def needs_config(self) -> NeedsSphinxConfig:
        return NeedsSphinxConfig(self.config)

    def finish(self) -> None:
        raise NotImplementedError("NeedsBuilderBase.finish() must be implemented in subclass")


class NeedsBuilder(NeedsBuilderBase):
    """Output the needs data as a JSON file,
    filtering by the ``needs_builder_filter`` config option if set,
    and writing to ``needs.json`` (or the ``needs_file`` config option if set)
    in the output folder.

    Note, in principle this builder could skip the write phase,
    since all need data is already finalised in the environment.
    However, this is not the case if ``export_id`` is set on any of the filters,
    since this data is currently only added in the write phase (by ``process_filters``).
    So for now we always run the write phase.
    """

    name = "needs"
    skip_write_phase = False

    def finish(self) -> None:
        # import here due to circular import
        from sphinx_needs.filter_common import filter_needs

        env = self.env
        filters = self.needs_data.get_or_create_filters()
        version = getattr(env.config, "version", "unset")
        needs_list = NeedsList(env.config, self.outdir, self.srcdir)

        if self.needs_config.file:
            needs_file = self.needs_config.file
            needs_list.load_json(needs_file)
        else:
            # check if needs.json file exists in conf.py directory
            needs_json = os.path.join(self.srcdir, "needs.json")
            if os.path.exists(needs_json):
                log.info("needs.json found, but will not be used because needs_file not configured.")

        # Clean needs_list from already stored needs of the current version.
        # This is needed as needs could have been removed from documentation and if this is the case,
        # removed needs would stay in needs_list, if list gets not cleaned.
        needs_list.wipe_version(version)

        filtered_needs: list[NeedsInfoType] = filter_needs(
            self.needs_config, self.needs_data.get_or_create_needs().values(), self.needs_config.builder_filter
        )

        for need in filtered_needs:
            needs_list.add_need(version, need)

        for need_filter in filters.values():
            if need_filter["export_id"]:
                needs_list.add_filter(version, need_filter)

        try:
            needs_list.write_json()
        except Exception as e:
            log.error(f"Error during writing json file: {e}")
        else:
            log.info("Needs successfully exported")


def build_needs_json(app: Sphinx, _exception: Exception) -> None:
    env = app.env

    if not NeedsSphinxConfig(env.config).build_json:
        return

    # Do not create an additional needs.json, if builder is already "needs".
    if isinstance(app.builder, NeedsBuilder):
        return

    try:
        needs_builder = NeedsBuilder(app, env)
    except TypeError:
        needs_builder = NeedsBuilder(app)
        needs_builder.set_environment(env)

    needs_builder.finish()


class NeedumlsBuilder(NeedsBuilderBase):
    """Write generated PlantUML input files to the output dir,
    that were generated by need directives,
    if they have a ``save`` field set,
    denoting the path relative to the output folder.
    """

    name = "needumls"

    def finish(self) -> None:
        needumls = self.needs_data.get_or_create_umls().values()

        for needuml in needumls:
            if needuml["save"]:
                puml_content = needuml["content_calculated"]
                # check if given save path dir exists
                save_path = os.path.join(self.outdir, needuml["save"])
                save_dir = os.path.dirname(save_path)
                if not os.path.exists(save_dir):
                    os.makedirs(save_dir, exist_ok=True)

                log.info(f"Storing needuml data to file {save_path}.")
                with open(save_path, "w") as f:
                    f.write(puml_content)


def build_needumls_pumls(app: Sphinx, _exception: Exception) -> None:
    env = app.env
    config = NeedsSphinxConfig(env.config)

    if not config.build_needumls:
        return

    # Do not create additional files for saved plantuml content, if builder is already "needumls".
    if isinstance(app.builder, NeedumlsBuilder):
        return

    # if other builder like html used together with config: needs_build_needumls
    if version_info[0] >= 5:
        needs_builder = NeedumlsBuilder(app, env)
        needs_builder.outdir = os.path.join(needs_builder.outdir, config.build_needumls)
    else:
        needs_builder = NeedumlsBuilder(app)
        needs_builder.outdir = os.path.join(needs_builder.outdir, config.build_needumls)
        needs_builder.set_environment(env)

    needs_builder.finish()


class NeedsIdBuilder(NeedsBuilderBase):
    """Output the needs data as multiple JSON files, one per need,
    filtering by the ``needs_builder_filter`` config option if set,
    and writing to ``needs.json`` (or the ``needs_file`` config option if set)
    in the output folder.
    """

    name = "needs_id"

    def finish(self) -> None:
        # import here due to circular import
        from sphinx_needs.filter_common import filter_needs

        env = self.env
        needs = self.needs_data.get_or_create_needs().values()
        version = getattr(env.config, "version", "unset")
        filtered_needs = filter_needs(self.needs_config, needs, self.needs_config.builder_filter)
        needs_build_json_per_id_path = self.needs_config.build_json_per_id_path
        needs_dir = os.path.join(self.outdir, needs_build_json_per_id_path)
        if not os.path.exists(needs_dir):
            os.makedirs(needs_dir, exist_ok=True)
        for need in filtered_needs:
            needs_list = NeedsList(env.config, self.outdir, self.srcdir)
            needs_list.wipe_version(version)
            needs_list.add_need(version, need)
            id = need["id"]
            try:
                file_name = f"{id}.json"
                needs_list.write_json(file_name, needs_dir)
            except Exception as e:
                log.error(f"Needs-ID Builder {id} error: {e}")
        log.info("Needs_id successfully exported")


def build_needs_id_json(app: Sphinx, _exception: Exception) -> None:
    env = app.env

    if not NeedsSphinxConfig(env.config).build_json_per_id:
        return

    # Do not create an additional needs_json for every needs_id, if builder is already "needs_id".
    if isinstance(app.builder, NeedsIdBuilder):
        return
    try:
        needs_id_builder = NeedsIdBuilder(app, env)
    except TypeError:
        needs_id_builder = NeedsIdBuilder(app)
        needs_id_builder.set_environment(env)

    needs_id_builder.finish()
