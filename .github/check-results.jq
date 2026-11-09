def require($condition; $message):
    if $condition then . else error($message) end;

($ENV | with_entries(select(.key | startswith("CI_EXPECT_")))
      | with_entries(.key |= (ltrimstr("CI_EXPECT_") | ascii_downcase))) as $expected
| ($ENV.CI_DIRECT_JOBS // "[]" | fromjson) as $direct
| require(type == "object" and keys == ($expected | keys); "Missing or unexpected job families")
| require(all($expected[]; . == "true" or . == "false") and any($expected[]; . == "true"); "No valid job selection")
| require(($direct | type) == "array" and all($direct[]; type == "string"); "Invalid direct jobs")
| require(($direct - ($expected | keys) | length) == 0; "Unknown direct job")
| to_entries[]
| .key as $family | .value as $group
| if $expected[$family] == "false" then
    require($group.result == "skipped"; $family + ": expected skipped")
    | "\($family): skipped"
  else
    require($group.result == "success"; $family + ": not successful")
    | if ($direct | index($family)) != null then
        require(($group.outputs | has("jobs") | not); $family + ": direct job exported workflow results")
        | "\($family): \($group.result)"
      else
        ($group.outputs.jobs | fromjson) as $jobs
        | ($group.outputs.inputs // "{}" | fromjson) as $inputs
        | $group.outputs.non_blocking as $non_blocking
        | require(($jobs | type) == "object" and ($jobs | length) > 0; $family + ": missing job results")
        | require(($inputs | type) == "object" and all($inputs[]; type == "boolean"); $family + ": invalid job selections")
        | require((($inputs | keys) - ($jobs | keys) | length) == 0; $family + ": unknown job selection")
        | require($non_blocking == null or ($jobs | has($non_blocking)); $family + ": unknown non-blocking job")
        | require(any($jobs | keys[]; $inputs[.] != false); $family + ": no jobs selected")
        | $jobs | to_entries[]
        | .key as $job | .value.result as $result
        | if $inputs[$job] == false then
            require($result == "skipped"; $family + "/" + $job + ": expected skipped")
          else
            require($result == "success" or ($result == "failure" and $job == $non_blocking);
                    $family + "/" + $job + ": " + ($result // "missing result"))
          end
        | "\($family)/\($job): \($result)"
      end
  end
