def require($condition; $message):
    if $condition then . else error($message) end;

($ENV | with_entries(select(.key | startswith("CI_EXPECT_")))
      | with_entries(.key |= (ltrimstr("CI_EXPECT_") | ascii_downcase))) as $expected
| require(type == "object" and keys == ($expected | keys); "Missing or unexpected job families")
| require(all($expected[]; . == "true" or . == "false") and any($expected[]; . == "true"); "No valid job selection")
| to_entries[]
| .key as $family | .value as $group
| if $expected[$family] == "false" then
    require($group.result == "skipped"; $family + ": expected skipped")
    | "\($family): skipped"
  else
    require($group.result == "success"; $family + ": not successful")
    | ($group.outputs.jobs | fromjson) as $jobs
    | ($group.outputs.inputs // "{}" | fromjson) as $inputs
    | $group.outputs.advisory as $advisory
    | require(($jobs | type) == "object" and ($jobs | length) > 0; $family + ": missing job results")
    | require(($inputs | type) == "object" and all($inputs[]; type == "boolean"); $family + ": invalid job selections")
    | require((($inputs | keys) - ($jobs | keys) | length) == 0; $family + ": unknown job selection")
    | require($advisory == null or ($jobs | has($advisory)); $family + ": unknown advisory job")
    | require(any($jobs | keys[]; $inputs[.] != false); $family + ": no jobs selected")
    | $jobs | to_entries[]
    | .key as $job | .value.result as $result
    | if $inputs[$job] == false then
        require($result == "skipped"; $family + "/" + $job + ": expected skipped")
      else
        require($result == "success" or ($result == "failure" and $job == $advisory);
                $family + "/" + $job + ": " + ($result // "missing result"))
      end
    | "\($family)/\($job): \($result)"
  end
